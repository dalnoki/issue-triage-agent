
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


# --------------------------------------------------
# Configuration
# --------------------------------------------------

REVIEW_FILE = Path("review_cases.json")
EVAL_FILE = Path("eval_cases.json")

VALID_DECISIONS = {
    "STAY",
    "TRANSFER",
    "REVIEW",
    "NEEDS_MORE_INFORMATION",
}

SOURCE_REPO = "getsentry/sentry"


# --------------------------------------------------
# JSON helpers
# --------------------------------------------------

def load_json(path):
    if not path.exists():
        raise FileNotFoundError(
            f"Missing file: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def save_json(path, data):
    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False,
        )

        file.write("\n")


def get_case(data, issue_number):
    for case in data["cases"]:
        if case["issue_number"] == issue_number:
            return case

    raise ValueError(
        f"Issue #{issue_number} is not in the review queue."
    )


# --------------------------------------------------
# Display queue
# --------------------------------------------------

def list_cases():
    data = load_json(REVIEW_FILE)

    print("\nEvaluation Review Queue")
    print("=" * 60)

    cases = data.get("cases", [])

    if not cases:
        print("No cases in the review queue.")
        return

    for case in cases:
        print(
            f"\n#{case['issue_number']} "
            f"[{case['status'].upper()}]"
        )

        print("Title:", case["name"])
        print("URL:", case["url"])

        print(
            "Human suggestion:",
            case.get("human_suggestion") or "Unknown",
        )

        print(
            "Agent suggestion:",
            case.get("agent_suggestion") or "Not tested",
        )

    pending = sum(
        case["status"] == "pending"
        for case in cases
    )

    disputed = sum(
        case["status"] == "disputed"
        for case in cases
    )

    confirmed = sum(
        case["status"] == "confirmed"
        for case in cases
    )

    print("\n--------------------------------")
    print("Pending:", pending)
    print("Disputed:", disputed)
    print("Confirmed:", confirmed)


# --------------------------------------------------
# Display one case
# --------------------------------------------------

def show_case(issue_number):
    data = load_json(REVIEW_FILE)
    case = get_case(data, issue_number)

    print("\n" + "=" * 60)

    print("Issue:", case["issue_number"])
    print("Title:", case["name"])
    print("URL:", case["url"])
    print("Status:", case["status"])

    print(
        "\nHuman suggestion:",
        case.get("human_suggestion"),
    )

    print(
        "Agent suggestion:",
        case.get("agent_suggestion"),
    )

    print(
        "Observed GitHub label:",
        case.get("github_label_observed"),
    )

    print("\nNotes:")
    print(case.get("notes", ""))

    if "confirmed_answer" in case:
        print("\nConfirmed answer:")
        print(
            json.dumps(
                case["confirmed_answer"],
                indent=2,
            )
        )


# --------------------------------------------------
# Mark a case as disputed
# --------------------------------------------------

def hold_case(issue_number, note):
    data = load_json(REVIEW_FILE)
    case = get_case(data, issue_number)

    if case["status"] == "confirmed":
        raise ValueError(
            "This case is already confirmed. "
            "Review the existing eval entry manually "
            "before changing its status."
        )

    case["status"] = "disputed"

    if note:
        case["notes"] = note

    save_json(REVIEW_FILE, data)

    print(
        f"Issue #{issue_number} remains in "
        "the review queue."
    )


# --------------------------------------------------
# Confirm an answer
# --------------------------------------------------

def confirm_case(issue_number):
    reviews = load_json(REVIEW_FILE)
    case = get_case(reviews, issue_number)

    if case["status"] == "confirmed":
        print("This issue is already confirmed.")
        return

    print(f"\nConfirming #{issue_number}")
    print("Title:", case["name"])
    print("URL:", case["url"])

    print("\nExisting suggestions:")
    print(
        "Human:",
        case.get("human_suggestion"),
    )
    print(
        "Agent:",
        case.get("agent_suggestion"),
    )
    print(
        "GitHub:",
        case.get("github_label_observed"),
    )

    print(
        "\nEnter your independently reviewed "
        "Support decision."
    )

    decision = input(
        "Decision (STAY/TRANSFER/REVIEW/"
        "NEEDS_MORE_INFORMATION): "
    ).strip().upper()

    if decision not in VALID_DECISIONS:
        raise ValueError(
            f"Invalid routing decision: {decision}"
        )

    if decision == "STAY":
        repository = SOURCE_REPO

        product_area = input(
            "Confirmed Product Area (full label): "
        ).strip()

        if not product_area.startswith("Product Area: "):
            raise ValueError(
                "Enter a full label such as "
                "'Product Area: Replays'."
            )

    elif decision == "TRANSFER":
        repository = input(
            "Confirmed destination repository: "
        ).strip()

        if (
            not repository.startswith("getsentry/")
            or repository == SOURCE_REPO
        ):
            raise ValueError(
                "Enter a destination such as "
                "'getsentry/self-hosted'."
            )

        product_area = None

    elif decision == "REVIEW":
        repository = SOURCE_REPO
        product_area = None

    else:
        repository = None
        product_area = None

    note = input(
        "Why is this the expected outcome? "
    ).strip()

    if not note:
        raise ValueError(
            "Please record a brief review rationale."
        )

    expected = {
        "name": case["name"],
        "url": case["url"],
        "expected_decision": decision,
        "expected_repository": repository,
        "expected_product_area": product_area,
    }

    print("\nProposed eval case:")
    print(json.dumps(expected, indent=2))

    confirmation = input(
        "\nConfirm this Support-reviewed answer "
        "and add it to fresh evals? (yes/no): "
    ).strip().lower()

    if confirmation != "yes":
        print("Cancelled. Nothing was saved.")
        return

    evals = load_json(EVAL_FILE)

    all_cases = (
        evals.get("regression", [])
        + evals.get("fresh", [])
    )

    if any(
        item["url"] == case["url"]
        for item in all_cases
    ):
        raise ValueError(
            "This issue already exists in eval_cases.json."
        )

    evals.setdefault("fresh", []).append(expected)

    case["status"] = "confirmed"
    case["human_suggestion"] = product_area
    case["confirmed_answer"] = expected
    case["review_rationale"] = note
    case["confirmed_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    # Save the evaluation first so that a case
    # isn't marked confirmed without an eval entry.
    save_json(EVAL_FILE, evals)
    save_json(REVIEW_FILE, reviews)

    print(
        f"\nIssue #{issue_number} confirmed "
        "and added to the fresh evaluation suite."
    )


# --------------------------------------------------
# CLI
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Manage human review of Sentry "
            "triage evaluation cases."
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "list",
        help="List pending and disputed cases.",
    )

    show_parser = subparsers.add_parser(
        "show",
        help="Show the details of a case.",
    )

    show_parser.add_argument(
        "issue_number",
        type=int,
    )

    confirm_parser = subparsers.add_parser(
        "confirm",
        help="Confirm and promote a reviewed case.",
    )

    confirm_parser.add_argument(
        "issue_number",
        type=int,
    )

    hold_parser = subparsers.add_parser(
        "hold",
        help="Mark a case as disputed.",
    )

    hold_parser.add_argument(
        "issue_number",
        type=int,
    )

    hold_parser.add_argument(
        "--note",
        default="",
    )

    args = parser.parse_args()

    if args.command == "list":
        list_cases()

    elif args.command == "show":
        show_case(args.issue_number)

    elif args.command == "confirm":
        confirm_case(args.issue_number)

    elif args.command == "hold":
        hold_case(
            args.issue_number,
            args.note,
        )


if __name__ == "__main__":
    main()
