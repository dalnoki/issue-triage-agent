import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("GITHUB_TOKEN")

if not token:
    raise RuntimeError("GITHUB_TOKEN is missing from .env")

url = "https://api.github.com/graphql"

query = """
query {
  repository(owner: "getsentry", name: "self-hosted") {
    issue(number: 4557) {
      title
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
      }
    }
  }
}
"""

response = requests.post(
    url,
    headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    },
    json={"query": query},
    timeout=20,
)

response.raise_for_status()

data = response.json()

if "errors" in data:
    print("GraphQL errors:", data["errors"])
else:
    issue = data["data"]["repository"]["issue"]

    print("Issue:", issue["title"])
    print("URL:", issue["url"])

    for event in issue["timelineItems"]["nodes"]:
        print("\nTRANSFER")
        print("Date:", event["createdAt"])
        print(
            "From:",
            event["fromRepository"]["nameWithOwner"]
            if event["fromRepository"]
            else "Unknown",
        )
        print("To: getsentry/self-hosted")