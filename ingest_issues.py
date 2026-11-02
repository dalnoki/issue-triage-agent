import os
import sqlite3
import requests


GITHUB_API = "https://api.github.com"
REPO = "getsentry/sentry"
DATABASE = "issues.db"


def get_headers():
    """
    Build headers for GitHub API requests.

    If GITHUB_TOKEN exists, use it.
    The script can still work without one, but authenticated
    requests are much better for a large historical import.
    """

    headers = {
        "Accept": "application/vnd.github+json",
    }

    token = os.getenv("GITHUB_TOKEN")

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


def create_database():
    conn = sqlite3.connect(DATABASE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            number INTEGER PRIMARY KEY,
            title TEXT,
            body TEXT,
            state TEXT,
            url TEXT,
            product_area TEXT
        )
    """)

    conn.commit()

    return conn


def get_product_area_labels():
    """
    Get every Product Area label currently defined
    in getsentry/sentry.
    """

    url = f"{GITHUB_API}/repos/{REPO}/labels"

    product_area_labels = []
    page = 1

    while True:
        print(f"Fetching labels page {page}...")

        response = requests.get(
            url,
            headers=get_headers(),
            params={
                "per_page": 100,
                "page": page,
            },
            timeout=30,
        )

        response.raise_for_status()

        labels = response.json()

        if not labels:
            break

        product_area_labels.extend(
            label["name"]
            for label in labels
            if label["name"].startswith("Product Area:")
        )

        if len(labels) < 100:
            break

        page += 1

    return product_area_labels


def fetch_issues_for_label(label):
    """
    Fetch all issues that currently have a particular
    Product Area label.
    """

    url = f"{GITHUB_API}/repos/{REPO}/issues"

    issues = []
    page = 1

    while True:
        print(f"  {label} — page {page}")

        response = requests.get(
            url,
            headers=get_headers(),
            params={
                "state": "all",
                "labels": label,
                "per_page": 100,
                "page": page,
            },
            timeout=30,
        )

        response.raise_for_status()

        batch = response.json()

        if not batch:
            break

        for issue in batch:

            # The GitHub Issues endpoint also returns PRs.
            if "pull_request" in issue:
                continue

            issues.append({
                "number": issue["number"],
                "title": issue["title"],
                "body": issue["body"] or "",
                "state": issue["state"],
                "url": issue["html_url"],
                "product_area": label,
            })

        if len(batch) < 100:
            break

        page += 1

    return issues


def save_issue(conn, issue):
    """
    Save immediately instead of waiting until the entire
    import finishes.

    This means if the script crashes halfway through,
    we don't lose everything we've already fetched.
    """

    conn.execute(
        """
        INSERT OR REPLACE INTO issues (
            number,
            title,
            body,
            state,
            url,
            product_area
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            issue["number"],
            issue["title"],
            issue["body"],
            issue["state"],
            issue["url"],
            issue["product_area"],
        ),
    )


def main():
    print("Creating database...")
    conn = create_database()

    print("\nGetting Product Area labels...")
    labels = get_product_area_labels()

    print(f"\nFound {len(labels)} Product Area labels.\n")

    total = 0

    for label in labels:
        print(f"\nFetching: {label}")

        try:
            issues = fetch_issues_for_label(label)

            for issue in issues:
                save_issue(conn, issue)

            # Commit after every Product Area.
            conn.commit()

            total += len(issues)

            print(f"  Found {len(issues)} issues.")

        except requests.RequestException as e:
            # Don't destroy everything just because one
            # Product Area failed.
            print(f"  ERROR fetching {label}: {e}")

    # Count unique issues actually stored.
    cursor = conn.execute("SELECT COUNT(*) FROM issues")
    stored = cursor.fetchone()[0]

    conn.close()

    print("\n-------------------------")
    print("Import complete")
    print(f"Fetched: {total}")
    print(f"Unique issues stored: {stored}")
    print(f"Database: {DATABASE}")
    print("-------------------------")


if __name__ == "__main__":
    main()