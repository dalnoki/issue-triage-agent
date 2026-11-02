import sqlite3

conn = sqlite3.connect("repo_routing.db")

rows = conn.execute("""
    SELECT destination_repo, COUNT(*)
    FROM repo_transfers
    GROUP BY destination_repo
    ORDER BY COUNT(*) DESC
""").fetchall()

print("\nTransfers per destination:")

for repo, count in rows:
    print(f"{count:4}  {repo}")

print("\nExample transfers:")

examples = conn.execute("""
    SELECT title, destination_repo, issue_url
    FROM repo_transfers
    LIMIT 10
""").fetchall()

for title, repo, url in examples:
    print(f"\n{title}")
    print(f"Destination: {repo}")
    print(f"URL: {url}")

conn.close()