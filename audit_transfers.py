"""Read-only audit of repo_transfers for multi-hop transfers.

Re-reads each stored issue's TransferredEvent timeline from GitHub and
reports where the stored destination_repo differs from the real first
destination out of getsentry/sentry.

Read-only: GitHub is only queried, and the database is opened read-only.
Usage:
    python audit_transfers.py            # prints report, writes audit JSON
    python audit_transfers.py --limit 5  # try a few rows first
"""

import argparse
import json
import sqlite3
import time

from transfer_path import SOURCE_REPO, fetch_transfer_info

DATABASE = "repo_routing.db"
REPORT_JSON = "transfer_audit_report.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)

    rows = conn.execute("""
        SELECT issue_url, title, destination_repo
        FROM repo_transfers
        WHERE source_repo = ?
        ORDER BY issue_url
    """, (SOURCE_REPO,)).fetchall()
    conn.close()

    if args.limit:
        rows = rows[:args.limit]

    results = []

    for index, (url, title, stored_destination) in enumerate(rows, 1):
        try:
            info = fetch_transfer_info(url)
        except Exception as error:
            info = {"error": str(error), "incomplete": True}

        results.append({
            "issue_url": url,
            "title": title,
            "stored_destination": stored_destination,
            **info,
        })

        print(f"[{index}/{len(rows)}] {url}", flush=True)
        time.sleep(0.2)

    multi_hop = [r for r in results if r.get("hop_count", 0) > 1]
    wrong = [
        r for r in results
        if r.get("first_destination_repo")
        and r["first_destination_repo"] != r["stored_destination"]
    ]
    problems = [
        r for r in results
        if r.get("error") or r.get("incomplete")
        or not r.get("first_destination_repo")
    ]

    print("\n==============================")
    print("Transfer audit")
    print("==============================")
    print(f"Rows audited:                     {len(results)}")
    print(f"Multi-hop (more than one move):   {len(multi_hop)}")
    print(f"Stored destination is wrong:      {len(wrong)}")
    print(f"Could not verify (see below):     {len(problems)}")

    if wrong:
        print("\nWrong stored destination:")
        for r in wrong:
            print(f"  {r['issue_url']}")
            print(f"    stored: {r['stored_destination']}")
            print(f"    real first destination: {r['first_destination_repo']}")
            print(f"    path: {' -> '.join(r['path'])}")

    other_multi = [r for r in multi_hop if r not in wrong]
    if other_multi:
        print("\nMulti-hop but stored destination happens to be right:")
        for r in other_multi:
            print(f"  {r['issue_url']}: {' -> '.join(r['path'])}")

    if problems:
        print("\nCould not verify:")
        for r in problems:
            reason = r.get("error") or (
                "timeline paginated" if r.get("incomplete")
                else "path never starts at " + SOURCE_REPO
            )
            print(f"  {r['issue_url']}: {reason}")

    with open(REPORT_JSON, "w") as handle:
        json.dump(results, handle, indent=2)

    print(f"\nFull report written to {REPORT_JSON}")


if __name__ == "__main__":
    main()
