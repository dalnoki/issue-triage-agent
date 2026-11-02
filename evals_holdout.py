
import argparse
import csv
import json

from datetime import datetime
from pathlib import Path

from main import triage_snapshot
from retrieval_audit import normalize_issue_url


# --------------------------------------------------
# Configuration
# --------------------------------------------------

# Historical-transfer holdout evaluation.
#
# Each case is an issue that WAS transferred out of
# getsentry/sentry. The agent sees only a frozen
# snapshot of the title and body as filed. It cannot
# fetch the live issue, its timeline, or its comments,
# and the holdout issues are removed from routing RAG.
#
# Results are compared with the historical action.
# A historical transfer is what happened on GitHub,
# not proof of correct ownership, so outcomes are
# reported as categories rather than pass/fail.

SNAPSHOTS_FILE = Path("holdout_snapshots.json")
REPORTS_FOLDER = Path("eval_reports") / "holdout"

LIVE_ISSUE_TOOLS = {
    "get_github_issue",
    "get_issue_routing_history",
}


# --------------------------------------------------
# Load snapshots
# --------------------------------------------------

def load_snapshots() -> dict:
    if not SNAPSHOTS_FILE.exists():
        raise FileNotFoundError(
            f"{SNAPSHOTS_FILE} not found. "
            "Run build_holdout_snapshots.py first."
        )

    with SNAPSHOTS_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def all_excluded_urls(snapshots: list[dict]) -> set[str]:
    """
    Exclude every holdout from every run, not only
    the case being triaged, so no holdout can serve
    as evidence for another.
    """
    return {
        url
        for snapshot in snapshots
        for url in snapshot["excluded_routing_urls"]
    }


# --------------------------------------------------
# Leakage verification
# --------------------------------------------------

def check_leakage(
    audit: dict,
    excluded: set[str],
    snapshot: dict,
    triage,
) -> list[str]:
    """
    Verify, from what actually happened during the
    run, that the holdout answer did not reach the
    agent.

    Returns a list of problems. Empty means clean.
    """
    problems = []
    excluded_normalized = {
        normalize_issue_url(url) for url in excluded
    }

    for call in audit["tool_calls"]:
        if call["tool"] in LIVE_ISSUE_TOOLS:
            problems.append(
                f"live issue tool called: {call['tool']}"
            )

    routing_retrievals = [
        entry for entry in audit["retrievals"]
        if entry["tool"] == "find_similar_routing_decisions"
    ]

    routing_tool_calls = [
        call for call in audit["tool_calls"]
        if call["tool"] == "find_similar_routing_decisions"
    ]

    # If the agent called the routing tool but the
    # tool logged nothing, the RETRIEVAL_LOG context
    # did not propagate, and we cannot trust the
    # exclusion either.
    if len(routing_retrievals) != len(routing_tool_calls):
        problems.append(
            "routing tool calls and logged retrievals differ "
            f"({len(routing_tool_calls)} vs "
            f"{len(routing_retrievals)}): context did not "
            "propagate into the tool"
        )

    for entry in routing_retrievals:
        seen = set(entry["excluded_urls"])

        if not excluded_normalized <= seen:
            problems.append(
                "routing search ran without the full "
                "exclusion set: context did not propagate"
            )

        for example in entry["returned"]:
            if normalize_issue_url(example["issue_url"]) in excluded_normalized:
                problems.append(
                    "excluded routing example returned: "
                    + example["issue_url"]
                )

    destination_url = normalize_issue_url(
        snapshot["historical_action"]["destination_url"]
    )

    output_text = triage.model_dump_json().lower()

    if destination_url in output_text:
        problems.append(
            "final output mentions the holdout destination URL"
        )

    return problems


# --------------------------------------------------
# Score one case
# --------------------------------------------------

def categorize(triage, historical: dict) -> str:
    """
    MATCH              TRANSFER to the historical destination
    WRONG_DESTINATION  TRANSFER somewhere else
    REVIEW             flagged as possibly belonging elsewhere
    STAY               kept in getsentry/sentry
    NEEDS_MORE_INFORMATION
    """
    if triage.decision == "TRANSFER":
        if (
            triage.suggested_repository
            == historical["destination_repository"]
        ):
            return "MATCH"

        return "WRONG_DESTINATION"

    return triage.decision


def evaluate_case(
    snapshot: dict,
    excluded: set[str],
) -> dict:
    historical = snapshot["historical_action"]

    print(f"\n{snapshot['case_id']}: {snapshot['title']}")

    record = {
        "case_id": snapshot["case_id"],
        "title": snapshot["title"],
        "historical_destination": historical["destination_repository"],
        "historical_url": historical["destination_url"],
        "transferred_at": historical["transferred_at"],
        "notes": snapshot.get("notes", ""),
        "outcome": "",
        "leakage_problems": [],
        "result": None,
        "audit": None,
        "error": "",
    }

    if not snapshot["snapshot_checks"]["usable"]:
        record["outcome"] = "SKIPPED"
        record["error"] = "; ".join(
            snapshot["snapshot_checks"]["problems"]
        )
        print("SKIPPED:", record["error"])
        return record

    try:
        triage, audit = triage_snapshot(
            snapshot,
            excluded_routing_urls=excluded,
        )

        record["result"] = triage.model_dump()
        record["audit"] = audit

        problems = check_leakage(
            audit,
            excluded,
            snapshot,
            triage,
        )

        record["leakage_problems"] = problems

        if problems:
            # A leaked run tells us nothing about the
            # agent, so it is not scored.
            record["outcome"] = "LEAK"
        else:
            record["outcome"] = categorize(triage, historical)

        print("Outcome:", record["outcome"])
        print(
            "Predicted:",
            triage.decision,
            triage.suggested_repository,
            triage.product_area or "",
        )
        print("Historical:", "TRANSFER", historical["destination_repository"])
        print(
            "Tools:",
            ", ".join(c["tool"] for c in audit["tool_calls"]),
        )

        for problem in problems:
            print("LEAKAGE:", problem)

    except Exception as error:
        record["outcome"] = "ERROR"
        record["error"] = str(error)
        print("ERROR:", error)

    return record


# --------------------------------------------------
# Reports
# --------------------------------------------------

CSV_FIELDS = [
    "case_id",
    "title",
    "outcome",
    "historical_destination",
    "predicted_decision",
    "predicted_repository",
    "predicted_product_area",
    "uncertainties",
    "routing_searches",
    "routing_examples_returned",
    "leakage_problems",
    "total_tokens",
    "reason",
    "historical_url",
    "transferred_at",
    "notes",
    "error",
]


def csv_row(record: dict) -> dict:
    result = record["result"] or {}
    audit = record["audit"] or {"retrievals": [], "usage": {}}

    routing = [
        entry for entry in audit["retrievals"]
        if entry["tool"] == "find_similar_routing_decisions"
    ]

    returned = [
        f"{example['decision']}:{example['issue_url']}"
        f"@{example['similarity']}"
        for entry in routing
        for example in entry["returned"]
    ]

    return {
        "case_id": record["case_id"],
        "title": record["title"],
        "outcome": record["outcome"],
        "historical_destination": record["historical_destination"],
        "predicted_decision": result.get("decision", ""),
        "predicted_repository": result.get("suggested_repository") or "",
        "predicted_product_area": result.get("product_area") or "",
        "uncertainties": " | ".join(result.get("uncertainties") or []),
        "routing_searches": len(routing),
        "routing_examples_returned": " | ".join(returned),
        "leakage_problems": " | ".join(record["leakage_problems"]),
        "total_tokens": audit["usage"].get("total_tokens", ""),
        "reason": result.get("reason", ""),
        "historical_url": record["historical_url"],
        "transferred_at": record["transferred_at"],
        "notes": record["notes"],
        "error": record["error"],
    }


def save_reports(
    records: list[dict],
    label: str,
    frozen_at: str,
) -> tuple[Path, Path]:
    REPORTS_FOLDER.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"holdout_{label}_{timestamp}"

    csv_path = REPORTS_FOLDER / f"{stem}.csv"
    json_path = REPORTS_FOLDER / f"{stem}.json"

    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(r) for r in records)

    # The JSON report keeps everything: full agent
    # output, every tool call, and every retrieval.
    with json_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "label": label,
                "run_at": datetime.now().isoformat(),
                "snapshots_frozen_at": frozen_at,
                "records": records,
            },
            file,
            indent=2,
            ensure_ascii=False,
        )
        file.write("\n")

    return csv_path, json_path


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Historical-transfer holdout evaluation. "
            "Offline: never fetches or modifies the "
            "holdout issues on GitHub."
        )
    )

    parser.add_argument(
        "--label",
        default="run",
        help="Name for this run, e.g. 'baseline'.",
    )

    parser.add_argument(
        "--case",
        action="append",
        help="Only run these case IDs (repeatable).",
    )

    args = parser.parse_args()

    data = load_snapshots()
    snapshots = data["snapshots"]

    # Exclusions always cover the whole holdout set,
    # even when running a single case.
    excluded = all_excluded_urls(snapshots)

    if args.case:
        snapshots = [
            s for s in snapshots if s["case_id"] in args.case
        ]

    print("Sentry Triage Holdout Evaluation")
    print("--------------------------------")
    print("Label:", args.label)
    print("Snapshots frozen at:", data["frozen_at"])
    print("Cases:", len(snapshots))
    print("Routing URLs excluded:", len(excluded))

    records = [
        evaluate_case(snapshot, excluded)
        for snapshot in snapshots
    ]

    counts = {}

    for record in records:
        counts[record["outcome"]] = counts.get(record["outcome"], 0) + 1

    print("\n--------------------------------")
    print("Outcomes (vs historical action)")
    print("--------------------------------")

    for outcome in [
        "MATCH",
        "WRONG_DESTINATION",
        "REVIEW",
        "STAY",
        "NEEDS_MORE_INFORMATION",
        "LEAK",
        "ERROR",
        "SKIPPED",
    ]:
        if counts.get(outcome):
            print(f"{outcome:24} {counts[outcome]}")

    scored = [
        r for r in records
        if r["outcome"] not in ("LEAK", "ERROR", "SKIPPED")
    ]

    if scored:
        matched = counts.get("MATCH", 0)
        flagged = matched + counts.get("WRONG_DESTINATION", 0) + counts.get("REVIEW", 0)

        print(
            f"\nMatched historical transfer: {matched}/{len(scored)}"
        )
        print(
            "Flagged as possibly leaving getsentry/sentry "
            f"(TRANSFER or REVIEW): {flagged}/{len(scored)}"
        )

    csv_path, json_path = save_reports(
        records,
        args.label,
        data["frozen_at"],
    )

    print("\nCSV report:", csv_path)
    print("Full JSON report:", json_path)


if __name__ == "__main__":
    main()
