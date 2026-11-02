"""Shared helpers for reconstructing an issue's full transfer path.

GitHub only exposes an issue's *current* repository in its URL. The real
history lives in the issue timeline as TransferredEvent items, each with a
`fromRepository`. Reading those events in order gives the whole path:

    events (oldest first):  from=A, from=B
    current repo:           C
    path:                   A -> B -> C

Everything here is read-only toward GitHub (GraphQL queries only).
"""

import json
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

SOURCE_REPO = "getsentry/sentry"

ISSUE_URL_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/issues/(\d+)$"
)

TIMELINE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      url
      timelineItems(first: 100, itemTypes: [TRANSFERRED_EVENT]) {
        nodes {
          ... on TransferredEvent {
            createdAt
            fromRepository { nameWithOwner }
          }
        }
        pageInfo { hasNextPage }
      }
    }
  }
}
"""


def graphql(query, variables):
    token = os.getenv("GITHUB_TOKEN")

    if not token:
        raise RuntimeError("GITHUB_TOKEN is missing from your .env file.")

    response = requests.post(
        "https://api.github.com/graphql",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"query": query, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()

    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL errors: {result['errors']}")

    return result["data"]


def parse_issue_url(url):
    match = ISSUE_URL_RE.match(url)

    if not match:
        raise ValueError(f"Unrecognized issue URL: {url}")

    owner, repo, number = match.groups()
    return owner, repo, int(number)


def build_path(events, current_repo):
    """Turn timeline events into an ordered list of repositories.

    `events` is a list of {"createdAt", "from"} dicts. A `from` of None means
    GitHub could not tell us the repository (deleted or private).
    """
    ordered = sorted(events, key=lambda e: e["createdAt"])
    return [e["from"] for e in ordered] + [current_repo]


def summarize_path(path, source_repo=SOURCE_REPO):
    """Derive the columns we store from a path.

    first_destination_repo is the repository the issue landed in right after
    it first left `source_repo`. It is None if the path never starts from
    source_repo, or if the hop is unreadable.
    """
    first_destination = None

    for index, repo in enumerate(path[:-1]):
        if repo == source_repo:
            first_destination = path[index + 1]
            break

    return {
        "first_destination_repo": first_destination,
        "final_repo": path[-1],
        "hop_count": len(path) - 1,
        "transfer_path": json.dumps(path),
    }


def fetch_transfer_info(issue_url, source_repo=SOURCE_REPO):
    """Re-read one issue's transfer timeline from GitHub.

    Returns a dict with the stored columns plus `incomplete` (True if the
    timeline was paginated or the issue could not be read).
    """
    owner, repo, number = parse_issue_url(issue_url)

    data = graphql(
        TIMELINE_QUERY,
        {"owner": owner, "repo": repo, "number": number},
    )

    repository = data["repository"]
    issue = repository["issue"] if repository else None

    if issue is None:
        return {"error": "issue not found", "incomplete": True}

    timeline = issue["timelineItems"]

    events = []

    for node in timeline["nodes"]:
        if not node:
            continue

        origin = node.get("fromRepository")

        events.append({
            "createdAt": node["createdAt"],
            "from": origin["nameWithOwner"] if origin else None,
        })

    # The issue node's own URL is authoritative for the current repository
    # (the stored URL may have been redirected after a later transfer).
    current_repo = "/".join(parse_issue_url(issue["url"])[:2])

    path = build_path(events, current_repo)

    info = summarize_path(path, source_repo)
    info["incomplete"] = bool(timeline["pageInfo"]["hasNextPage"])
    info["path"] = path
    return info
