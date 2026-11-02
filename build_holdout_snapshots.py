
import argparse
import json
import math
import os
import sqlite3

from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from retrieval_audit import normalize_issue_url

load_dotenv()


# --------------------------------------------------
# Configuration
# --------------------------------------------------

# This script runs ONCE per holdout set. It reads each
# case from GitHub (read-only), checks that it is a
# clean single transfer out of getsentry/sentry, and
# freezes what the agent is allowed to see into
# holdout_snapshots.json.
#
# evals_holdout.py never contacts GitHub about these
# issues. It only reads the frozen snapshots.

CASES_FILE = Path("holdout_cases.json")
SNAPSHOTS_FILE = Path("holdout_snapshots.json")

ROUTING_DATABASE = "repo_routing.db"
PRODUCT_AREA_DATABASE = "issues.db"

SOURCE_REPO = "getsentry/sentry"

# Routing examples this similar to a holdout are
# treated as duplicates and excluded too.
DUPLICATE_SIMILARITY = 0.95

SNAPSHOT_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    nameWithOwner
    issue(number: $number) {
      url
      title
      body
      createdAt
      lastEditedAt

      userContentEdits(first: 100) {
        nodes {
          editedAt
        }
      }

      timelineItems(
        first: 100
        itemTypes: [TRANSFERRED_EVENT]
      ) {
        nodes {
          ... on TransferredEvent {
            createdAt
            fromRepository {
              nameWithOwner
            }
          }
        }
        pageInfo {
          hasNextPage
        }
      }
    }
  }
}
"""


# --------------------------------------------------
# GitHub API (read-only)
# --------------------------------------------------

def github_graphql(query, variables):
    token = os.getenv("GITHUB_TOKEN")

    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is missing from .env"
        )

    response = requests.post(
        "https://api.github.com/graphql",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "query": query,
            "variables": variables,
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if result.get("errors"):
        raise RuntimeError(
            f"GitHub GraphQL error: {result['errors']}"
        )

    return result["data"]


def parse_issue_url(url: str) -> tuple[str, str, int]:
    parts = url.rstrip("/").split("/")

    if len(parts) < 7 or parts[5] != "issues":
        raise ValueError(f"Not a GitHub issue URL: {url}")

    return parts[3], parts[4], int(parts[6])


# --------------------------------------------------
# Leakage checks against local datasets
# --------------------------------------------------

def cosine_similarity(a, b):
    dot_product = sum(x * y for x, y in zip(a, b))
    magnitude_a = math.sqrt(sum(x * x for x in a))
    magnitude_b = math.sqrt(sum(x * x for x in b))

    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0

    return dot_product / (magnitude_a * magnitude_b)


def find_routing_neighbours(url: str) -> dict:
    """
    Compare the holdout's stored routing embedding
    with every other routing example.

    Uses embeddings already in repo_routing.db, so no
    API calls are needed.
    """
    conn = sqlite3.connect(ROUTING_DATABASE)

    try:
        rows = conn.execute("""
            SELECT issue_url, title, embedding
            FROM routing_examples
            WHERE embedding IS NOT NULL
        """).fetchall()
    finally:
        conn.close()

    target = normalize_issue_url(url)
    own = None

    for issue_url, _, embedding_json in rows:
        if normalize_issue_url(issue_url) == target:
            own = json.loads(embedding_json)
            break

    if own is None:
        return {
            "in_routing_dataset": False,
            "duplicates": [],
            "nearest": [],
        }

    neighbours = []

    for issue_url, title, embedding_json in rows:
        if normalize_issue_url(issue_url) == target:
            continue

        similarity = cosine_similarity(
            own,
            json.loads(embedding_json),
        )

        neighbours.append({
            "issue_url": issue_url,
            "title": title,
            "similarity": round(similarity, 3),
        })

    neighbours.sort(
        key=lambda item: item["similarity"],
        reverse=True,
    )

    return {
        "in_routing_dataset": True,
        "duplicates": [
            item for item in neighbours
            if item["similarity"] >= DUPLICATE_SIMILARITY
        ],
        "nearest": neighbours[:3],
    }


def find_product_area_title_matches(title: str) -> list[dict]:
    """
    The holdout no longer has a getsentry/sentry
    number, so check issues.db by exact title instead.
    """
    conn = sqlite3.connect(PRODUCT_AREA_DATABASE)

    try:
        rows = conn.execute("""
            SELECT number, url, product_area
            FROM issues
            WHERE LOWER(TRIM(title)) = LOWER(TRIM(?))
        """, (title,)).fetchall()
    finally:
        conn.close()

    return [
        {
            "number": number,
            "url": url,
            "product_area": product_area,
        }
        for number, url, product_area in rows
    ]


# --------------------------------------------------
# Build one snapshot
# --------------------------------------------------

def build_snapshot(case: dict) -> dict:
    url = case["destination_url"]
    owner, repo, number = parse_issue_url(url)

    data = github_graphql(
        SNAPSHOT_QUERY,
        {
            "owner": owner,
            "repo": repo,
            "number": number,
        },
    )

    repository = data.get("repository")

    if not repository or not repository.get("issue"):
        raise RuntimeError(f"Could not read {url}")

    issue = repository["issue"]
    current_repo = repository["nameWithOwner"]

    problems = []

    timeline = issue["timelineItems"]

    if timeline["pageInfo"]["hasNextPage"]:
        problems.append("transfer history truncated")

    transfers = sorted(
        (
            {
                "date": event["createdAt"],
                "from_repository": (
                    event["fromRepository"]["nameWithOwner"]
                    if event.get("fromRepository") else None
                ),
            }
            for event in timeline["nodes"]
            if event
        ),
        key=lambda event: event["date"],
    )

    out_of_source = [
        event for event in transfers
        if event["from_repository"] == SOURCE_REPO
    ]

    if len(out_of_source) != 1:
        problems.append(
            f"expected 1 transfer out of {SOURCE_REPO}, "
            f"found {len(out_of_source)}"
        )

    # A clean case is one where the transfer out of
    # getsentry/sentry was the last hop. Otherwise the
    # "destination" in our dataset is really a later
    # hop, not the decision made in getsentry/sentry.
    if transfers and transfers[-1]["from_repository"] != SOURCE_REPO:
        problems.append(
            "transfer out of getsentry/sentry was not the "
            "final hop; full path: "
            + " -> ".join(
                str(event["from_repository"])
                for event in transfers
            )
            + f" -> {current_repo}"
        )

    transferred_at = (
        out_of_source[0]["date"] if out_of_source else None
    )

    # The body we freeze must be the body as it was
    # when the transfer decision was made.
    edits_after_transfer = [
        edit["editedAt"]
        for edit in issue["userContentEdits"]["nodes"]
        if transferred_at and edit["editedAt"] > transferred_at
    ]

    if edits_after_transfer:
        problems.append(
            "body edited after transfer: "
            + ", ".join(edits_after_transfer)
        )

    neighbours = find_routing_neighbours(url)
    title_matches = find_product_area_title_matches(
        issue["title"]
    )

    if title_matches:
        problems.append(
            "title also present in issues.db: "
            + ", ".join(m["url"] for m in title_matches)
        )

    excluded_routing_urls = [url] + [
        item["issue_url"]
        for item in neighbours["duplicates"]
    ]

    return {
        "case_id": case["case_id"],
        "notes": case.get("notes", ""),

        # What the agent sees.
        "title": issue["title"],
        "body": issue["body"] or "",

        # Hidden from the agent. Used only for scoring.
        "historical_action": {
            "decision": "TRANSFER",
            "source_repository": SOURCE_REPO,
            "destination_repository": current_repo,
            "destination_url": issue["url"],
            "transferred_at": transferred_at,
            "transfer_path": transfers,
        },

        # Leakage controls.
        "excluded_routing_urls": excluded_routing_urls,

        "snapshot_checks": {
            "created_at": issue["createdAt"],
            "last_edited_at": issue["lastEditedAt"],
            "body_edited_after_transfer": bool(
                edits_after_transfer
            ),
            "in_routing_dataset": neighbours["in_routing_dataset"],
            "nearest_routing_examples": neighbours["nearest"],
            "duplicate_routing_examples": neighbours["duplicates"],
            "product_area_title_matches": title_matches,
            "problems": problems,
            "usable": not problems,
        },
    }


# --------------------------------------------------
# Main
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Freeze historical-transfer holdout snapshots. "
            "Read-only: never modifies GitHub."
        )
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing snapshot file.",
    )

    args = parser.parse_args()

    if SNAPSHOTS_FILE.exists() and not args.force:
        print(
            f"{SNAPSHOTS_FILE} already exists. Snapshots are "
            "frozen on purpose; pass --force to rebuild."
        )
        return

    with CASES_FILE.open("r", encoding="utf-8") as file:
        cases = json.load(file)["cases"]

    snapshots = []

    for case in cases:
        print(f"\n{case['case_id']}: {case['destination_url']}")

        snapshot = build_snapshot(case)
        checks = snapshot["snapshot_checks"]

        print("  Title:", snapshot["title"])
        print(
            "  Transferred:",
            snapshot["historical_action"]["transferred_at"],
            "->",
            snapshot["historical_action"]["destination_repository"],
        )
        print("  Excluded from routing RAG:")

        for url in snapshot["excluded_routing_urls"]:
            print("   -", url)

        print("  Nearest remaining routing examples:")

        for item in checks["nearest_routing_examples"]:
            print(
                f"   {item['similarity']:.3f} "
                f"{item['title'][:60]}"
            )

        if checks["usable"]:
            print("  OK")
        else:
            for problem in checks["problems"]:
                print("  PROBLEM:", problem)

        snapshots.append(snapshot)

    output = {
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "source_cases_file": str(CASES_FILE),
        "duplicate_similarity_threshold": DUPLICATE_SIMILARITY,
        "snapshots": snapshots,
    }

    with SNAPSHOTS_FILE.open("w", encoding="utf-8") as file:
        json.dump(output, file, indent=2, ensure_ascii=False)
        file.write("\n")

    usable = sum(
        s["snapshot_checks"]["usable"] for s in snapshots
    )

    print(f"\nUsable snapshots: {usable}/{len(snapshots)}")
    print("Saved to:", SNAPSHOTS_FILE)


if __name__ == "__main__":
    main()
