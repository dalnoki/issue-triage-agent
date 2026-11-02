
import random
import sqlite3
from collections import defaultdict

# ------------------------------------------
# Configuration
# ------------------------------------------

PRODUCT_AREA_DATABASE = "issues.db"
ROUTING_DATABASE = "repo_routing.db"

SOURCE_REPO = "getsentry/sentry"
STAY_SAMPLE_SIZE = 50
RANDOM_SEED = 42


# ------------------------------------------
# Create routing examples table
# ------------------------------------------

def create_routing_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS routing_examples (
            example_id TEXT PRIMARY KEY,
            issue_url TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            decision TEXT NOT NULL,
            source_repo TEXT NOT NULL,
            destination_repo TEXT,
            product_area TEXT,
            evidence_status TEXT NOT NULL,
            embedding TEXT,
            CHECK (decision IN ('STAY', 'TRANSFER'))
        )
    """)

    conn.commit()


# ------------------------------------------
# Import verified transfers
# ------------------------------------------

def import_transfers(conn):
    # The label for a routing example is where the issue went when it first
    # left getsentry/sentry, not where it ended up after later transfers.
    # Rows not yet backfilled fall back to destination_repo.
    transfers = conn.execute("""
        SELECT
            issue_url,
            title,
            body,
            source_repo,
            COALESCE(first_destination_repo, destination_repo),
            hop_count
        FROM repo_transfers
        WHERE source_repo = ?
    """, (SOURCE_REPO,)).fetchall()

    added = 0
    unverified = 0

    for url, title, body, source, destination, hop_count in transfers:
        if not title or not destination:
            continue

        if hop_count is None:
            unverified += 1

        result = conn.execute("""
            INSERT OR IGNORE INTO routing_examples (
                example_id,
                issue_url,
                title,
                body,
                decision,
                source_repo,
                destination_repo,
                product_area,
                evidence_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"transfer:{url}",
            url,
            title,
            body or "",
            "TRANSFER",
            source,
            destination,
            None,
            "confirmed_transfer",
        ))

        added += result.rowcount

    conn.commit()

    if unverified:
        print(
            f"WARNING: {unverified} transfers have no path data yet. "
            "Run: python ingest_transfers.py --backfill"
        )

    return added


def report_stale_examples(conn):
    """Report (never change) existing examples with a wrong label."""
    rows = conn.execute("""
        SELECT e.issue_url, e.destination_repo, t.first_destination_repo
        FROM routing_examples e
        JOIN repo_transfers t ON t.issue_url = e.issue_url
        WHERE e.decision = 'TRANSFER'
          AND t.first_destination_repo IS NOT NULL
          AND e.destination_repo != t.first_destination_repo
    """).fetchall()

    if rows:
        print(
            f"\n{len(rows)} existing routing_examples have a stale "
            "destination (NOT modified; decide before rebuilding):"
        )
        for url, current, correct in rows:
            print(f"  {url}: {current} -> should be {correct}")


# ------------------------------------------
# Sample provisional STAY examples
# ------------------------------------------

def sample_stay_issues(routing_conn):
    product_conn = sqlite3.connect(
        PRODUCT_AREA_DATABASE
    )

    try:
        issues = product_conn.execute("""
            SELECT
                number,
                title,
                body,
                product_area
            FROM issues
            WHERE title IS NOT NULL
              AND TRIM(title) != ''
              AND product_area IS NOT NULL
              AND TRIM(product_area) != ''
        """).fetchall()

    finally:
        product_conn.close()

    # Group issues by Product Area so our
    # sample includes different product areas.
    by_product_area = defaultdict(list)

    for number, title, body, product_area in issues:
        by_product_area[product_area].append(
            (number, title, body, product_area)
        )

    rng = random.Random(RANDOM_SEED)

    groups = list(by_product_area.values())

    for group in groups:
        rng.shuffle(group)

    rng.shuffle(groups)

    selected = []

    # Select one example from each group in
    # successive rounds.
    while len(selected) < STAY_SAMPLE_SIZE:
        found_any = False

        for group in groups:
            if len(selected) >= STAY_SAMPLE_SIZE:
                break

            if not group:
                continue

            selected.append(group.pop())
            found_any = True

        if not found_any:
            break

    added = 0

    for number, title, body, product_area in selected:
        url = (
            f"https://github.com/{SOURCE_REPO}/"
            f"issues/{number}"
        )

        result = routing_conn.execute("""
            INSERT OR IGNORE INTO routing_examples (
                example_id,
                issue_url,
                title,
                body,
                decision,
                source_repo,
                destination_repo,
                product_area,
                evidence_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"stay:{number}",
            url,
            title,
            body or "",
            "STAY",
            SOURCE_REPO,
            None,
            product_area,
            "provisional_stay",
        ))

        added += result.rowcount

    routing_conn.commit()
    return added


# ------------------------------------------
# Display summary
# ------------------------------------------

def print_summary(conn):
    print("\nRouting dataset summary")
    print("----------------------------")

    rows = conn.execute("""
        SELECT
            decision,
            evidence_status,
            COUNT(*)
        FROM routing_examples
        GROUP BY decision, evidence_status
        ORDER BY decision
    """).fetchall()

    for decision, status, count in rows:
        print(
            f"{decision:8} "
            f"{status:22} "
            f"{count}"
        )

    print("\nTransfer destinations:")

    rows = conn.execute("""
        SELECT destination_repo, COUNT(*)
        FROM routing_examples
        WHERE decision = 'TRANSFER'
        GROUP BY destination_repo
        ORDER BY COUNT(*) DESC
    """).fetchall()

    for repo, count in rows:
        print(f"  {count:4}  {repo}")

    total = conn.execute("""
        SELECT COUNT(*)
        FROM routing_examples
    """).fetchone()[0]

    print("\nTotal routing examples:", total)
    print("Database:", ROUTING_DATABASE)


# ------------------------------------------
# Main
# ------------------------------------------

def main():
    conn = sqlite3.connect(ROUTING_DATABASE)

    try:
        create_routing_table(conn)

        transfers_added = import_transfers(conn)
        stays_added = sample_stay_issues(conn)

        print(
            "New confirmed transfers added:",
            transfers_added,
        )

        print(
            "New provisional STAY examples added:",
            stays_added,
        )

        report_stale_examples(conn)
        print_summary(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
