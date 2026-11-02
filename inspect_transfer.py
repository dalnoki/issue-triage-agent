import os
import requests
from dotenv import load_dotenv

load_dotenv()

url = (
    "https://api.github.com/repos/"
    "getsentry/self-hosted/issues/4557/timeline"
)

headers = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

if os.getenv("GITHUB_TOKEN"):
    headers["Authorization"] = (
        f"Bearer {os.environ['GITHUB_TOKEN']}"
    )

response = requests.get(
    url,
    headers=headers,
    params={"per_page": 100},
    timeout=20,
)

response.raise_for_status()

events = response.json()

print(f"Timeline events returned: {len(events)}")

for event in events:
    if "transfer" in event.get("event", "").lower():
        print("\nTRANSFER EVENT FOUND")
        print("Event type:", event.get("event"))
        print("Date:", event.get("created_at"))
        print("Event data:", event)