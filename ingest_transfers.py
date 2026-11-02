
import os
import sqlite3
import sys
import time
import requests

from dotenv import load_dotenv

from transfer_path import (
    build_path,
    fetch_transfer_info,
    parse_issue_url,
    summarize_path,
)

load_dotenv()

# --------------------------------------------------
# Configuration
# --------------------------------------------------

SOURCE_REPO = "getsentry/sentry"
DATABASE = "repo_routing.db"

# New repositories to investigate.
# We already scanned self-hosted, Python, and JavaScript.
DESTINATION_REPOS = [
    "getsentry/sentry-java",
    "getsentry/sentry-cocoa",
    "getsentry/sentry-dotnet",
    "getsentry/sentry-react-native",
]

# Scan up to 100 issues per repository per run.
ISSUES_PER_RUN = 100

# Smaller requests reduce GraphQL query complexity.
PAGE_SIZE = 25

token = os.getenv("GITHUB_TOKEN")

if not token:
    raise RuntimeError(
        "GITHUB_TOKEN is missing from your .env file."
    )


# --------------------------------------------------
# GitHub API
# --------------------------------------------------

def graphql(query, variables):
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
            f"GitHub GraphQL errors: {result['errors']}"
        )

    return result["data"]


# --------------------------------------------------
# Database
# --------------------------------------------------

def create_database():
    conn = sqlite3.connect(DATABASE)

    # Existing transfer records are preserved.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS repo_transfers (
            issue_url TEXT PRIMARY KEY,
            title TEXT,
            body TEXT,
            source_repo TEXT,
            destination_repo TEXT,
            transferred_at TEXT
        )
    """)

    # Multi-hop support. destination_repo stays as the issue's *current*
    # repository (unchanged, so existing data is preserved); these columns
    # record the real path. Older databases get them added in place.
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(repo_transfers)")
    }

    for column, ddl in [
        ("first_destination_repo", "TEXT"),
        ("final_repo", "TEXT"),
        ("hop_count", "INTEGER"),
        ("transfer_path", "TEXT"),
    ]:
        if column not in existing:
            conn.execute(
                f"ALTER TABLE repo_transfers ADD COLUMN {column} {ddl}"
            )

    # New table for remembering pagination progress.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_progress (
            repo_name TEXT PRIMARY KEY,
            cursor TEXT,
            issues_inspected INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    return conn


def get_progress(conn, repo_name):
    row = conn.execute(
        """
        SELECT cursor, issues_inspected, completed
        FROM scan_progress
        WHERE repo_name = ?
        """,
        (repo_name,),
    ).fetchone()

    if row is None:
        return None, 0, False

    cursor, inspected, completed = row

    return cursor, inspected, bool(completed)


def save_progress(
    conn,
    repo_name,
    cursor,
    inspected,
    completed,
):
    conn.execute(
        """
        INSERT INTO scan_progress (
            repo_name,
            cursor,
            issues_inspected,
            completed
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(repo_name)
        DO UPDATE SET
            cursor = excluded.cursor,
            issues_inspected = excluded.issues_inspected,
            completed = excluded.completed
        """,
        (
            repo_name,
            cursor,
            inspected,
            int(completed),
        ),
    )


# --------------------------------------------------
# GraphQL query
# --------------------------------------------------

QUERY = """
query(
  $owner: String!
  $repo: String!
  $count: Int!
  $cursor: String
) {
  repository(owner: $owner, name: $repo) {
    issues(
      first: $count
      after: $cursor
      orderBy: {
        field: UPDATED_AT
        direction: DESC
      }
    ) {
      nodes {
        title
        body
        url

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

      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""


# --------------------------------------------------
# Repository scan
# --------------------------------------------------

def inspect_repository(repo_name, conn):
    owner, repo = repo_name.split("/")

    cursor, total_inspected, completed = get_progress(
        conn,
        repo_name,
    )

    print(f"\nInspecting {repo_name}...")

    if completed:
        print("  Already completely scanned. Skipping.")
        return

    if total_inspected:
        print(
            f"  Resuming after {total_inspected} "
            "previously inspected issues."
        )

    inspected_this_run = 0
    new_transfers = 0
    existing_transfers = 0

    while inspected_this_run < ISSUES_PER_RUN:
        count = min(
            PAGE_SIZE,
            ISSUES_PER_RUN - inspected_this_run,
        )

        print(
            f"  Fetching next {count} issues..."
        )

        data = graphql(
            QUERY,
            {
                "owner": owner,
                "repo": repo,
                "count": count,
                "cursor": cursor,
            },
        )

        repository = data["repository"]

        if repository is None:
            print("  Repository unavailable. Skipping.")
            return

        connection = repository["issues"]
        issues = connection["nodes"]

        if not issues:
            print("  No more issues found.")

            save_progress(
                conn,
                repo_name,
                cursor,
                total_inspected,
                True,
            )
            conn.commit()
            break

        # Save issue records and pagination progress
        # together, so a successful page can be resumed.
        try:
            for issue in issues:
                if issue is None:
                    continue

                timeline = issue["timelineItems"]

                if timeline["pageInfo"]["hasNextPage"]:
                    print(
                        "  WARNING: Transfer history "
                        "may be incomplete:",
                        issue["url"],
                    )

                # Reconstruct the whole path once per issue: every
                # TransferredEvent's fromRepository, oldest first, then
                # the repository the issue lives in now.
                events = [
                    {
                        "createdAt": e["createdAt"],
                        "from": (
                            e["fromRepository"]["nameWithOwner"]
                            if e.get("fromRepository") else None
                        ),
                    }
                    for e in timeline["nodes"]
                    if e is not None
                ]
                path_info = summarize_path(
                    build_path(events, repo_name)
                )

                for event in timeline["nodes"]:
                    if event is None:
                        continue

                    source = event.get("fromRepository")

                    if not source:
                        continue

                    if source["nameWithOwner"] != SOURCE_REPO:
                        continue

                    db_cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO repo_transfers (
                            issue_url,
                            title,
                            body,
                            source_repo,
                            destination_repo,
                            transferred_at,
                            first_destination_repo,
                            final_repo,
                            hop_count,
                            transfer_path
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            issue["url"],
                            issue["title"],
                            issue["body"] or "",
                            SOURCE_REPO,
                            repo_name,
                            event["createdAt"],
                            path_info["first_destination_repo"],
                            path_info["final_repo"],
                            path_info["hop_count"],
                            path_info["transfer_path"],
                        ),
                    )

                    if db_cursor.rowcount == 1:
                        new_transfers += 1
                        print(
                            "  NEW TRANSFER:",
                            issue["url"],
                        )
                    else:
                        existing_transfers += 1

            inspected_this_run += len(issues)
            total_inspected += len(issues)

            page_info = connection["pageInfo"]
            has_next = page_info["hasNextPage"]
            next_cursor = page_info["endCursor"]

            if has_next and not next_cursor:
                raise RuntimeError(
                    "GitHub reported another page "
                    "but did not provide a cursor."
                )

            cursor = next_cursor

            save_progress(
                conn,
                repo_name,
                cursor,
                total_inspected,
                not has_next,
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        print(
            f"  Progress: {total_inspected} total issues, "
            f"{new_transfers} new transfers"
        )

        if not has_next:
            print("  Reached the end of the repository.")
            break

    print(
        f"\nFinished {repo_name}:"
        f"\n  Inspected this run: {inspected_this_run}"
        f"\n  New transfers: {new_transfers}"
        f"\n  Previously stored: {existing_transfers}"
    )


# --------------------------------------------------
# Summary
# --------------------------------------------------

def print_summary(conn):
    print("\n--------------------------------")
    print("Transfers per destination")
    print("--------------------------------")

    rows = conn.execute("""
        SELECT destination_repo, COUNT(*)
        FROM repo_transfers
        GROUP BY destination_repo
        ORDER BY COUNT(*) DESC
    """).fetchall()

    for repo, count in rows:
        print(f"{count:4}  {repo}")

    total = conn.execute(
        "SELECT COUNT(*) FROM repo_transfers"
    ).fetchone()[0]

    print("\nTotal unique transfers:", total)

    print("\nScan progress:")

    rows = conn.execute("""
        SELECT repo_name, issues_inspected, completed
        FROM scan_progress
        ORDER BY repo_name
    """).fetchall()

    for repo, inspected, completed in rows:
        status = "complete" if completed else "paused"
        print(f"  {repo}: {inspected} ({status})")

    print("\nDatabase:", DATABASE)


def backfill_paths(conn):
    """Fill the path columns for rows stored before they existed.

    Reads GitHub (read-only) and updates only the four new columns;
    destination_repo and every other column are left untouched.
    """
    rows = conn.execute("""
        SELECT issue_url
        FROM repo_transfers
        WHERE transfer_path IS NULL
    """).fetchall()

    print(f"Backfilling {len(rows)} rows...")

    for (url,) in rows:
        info = fetch_transfer_info(url, SOURCE_REPO)

        if info.get("error") or info.get("incomplete"):
            print("  SKIPPED (could not verify):", url)
            continue

        conn.execute(
            """
            UPDATE repo_transfers
            SET first_destination_repo = ?,
                final_repo = ?,
                hop_count = ?,
                transfer_path = ?
            WHERE issue_url = ?
            """,
            (
                info["first_destination_repo"],
                info["final_repo"],
                info["hop_count"],
                info["transfer_path"],
                url,
            ),
        )

        if info["hop_count"] > 1:
            print("  MULTI-HOP:", url, info["transfer_path"])

        conn.commit()
        time.sleep(0.2)


def main():
    conn = create_database()

    try:
        if "--backfill" in sys.argv:
            backfill_paths(conn)
            print_summary(conn)
            return

        for repo in DESTINATION_REPOS:
            inspect_repository(repo, conn)

        print_summary(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
