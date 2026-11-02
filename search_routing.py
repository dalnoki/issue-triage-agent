
import json
import math
import sqlite3

from dotenv import load_dotenv
from openai import OpenAI
from agents import function_tool

from build_routing_embeddings import (
    prepare_routing_text,
    EMBEDDING_MODEL,
)

from retrieval_audit import (
    EXCLUDED_ROUTING_URLS,
    normalize_issue_url,
    record_retrieval,
)

load_dotenv()

DATABASE = "repo_routing.db"


def cosine_similarity(a, b):
    dot_product = sum(x * y for x, y in zip(a, b))
    magnitude_a = math.sqrt(sum(x * x for x in a))
    magnitude_b = math.sqrt(sum(x * x for x in b))

    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0

    return dot_product / (magnitude_a * magnitude_b)


def search_routing_examples(
    title: str,
    body: str,
    limit_per_decision: int = 3,
    exclude_issue_url: str | None = None,
) -> dict:
    """
    Retrieve similar STAY and TRANSFER decisions.

    Similarity scores represent semantic relatedness,
    not confidence that a routing decision is correct.

    Excludes the URL passed by the caller, plus any
    URLs in the EXCLUDED_ROUTING_URLS context (used by
    holdout evaluations). URLs are normalized first.
    """
    client = OpenAI()

    query_text = prepare_routing_text(title, body)

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query_text,
    )

    query_embedding = response.data[0].embedding

    excluded = {
        normalize_issue_url(url)
        for url in EXCLUDED_ROUTING_URLS.get()
    }

    if exclude_issue_url:
        excluded.add(normalize_issue_url(exclude_issue_url))

    conn = sqlite3.connect(DATABASE)

    try:
        rows = conn.execute("""
            SELECT
                issue_url,
                title,
                body,
                decision,
                destination_repo,
                evidence_status,
                embedding
            FROM routing_examples
            WHERE embedding IS NOT NULL
        """).fetchall()
    finally:
        conn.close()

    results = {
        "STAY": [],
        "TRANSFER": [],
    }

    skipped = 0

    for (
        issue_url,
        historical_title,
        historical_body,
        decision,
        destination_repo,
        evidence_status,
        embedding_json,
    ) in rows:

        if normalize_issue_url(issue_url) in excluded:
            skipped += 1
            continue

        if decision not in results:
            continue

        historical_embedding = json.loads(embedding_json)

        similarity = cosine_similarity(
            query_embedding,
            historical_embedding,
        )

        # Strip reporter-selected metadata from the
        # excerpt as well, not just the embeddings.
        clean_text = prepare_routing_text(
            historical_title,
            historical_body,
        )

        excerpt = clean_text[:700]

        results[decision].append({
            "title": historical_title,
            "excerpt": excerpt,
            "issue_url": issue_url,
            "decision": decision,
            "destination_repo": destination_repo,
            "evidence_status": evidence_status,
            "similarity": round(similarity, 3),
        })

    limit_per_decision = max(
        1,
        min(limit_per_decision, 5),
    )

    for decision in results:
        results[decision].sort(
            key=lambda example: example["similarity"],
            reverse=True,
        )

        results[decision] = results[decision][
            :limit_per_decision
        ]

    record_retrieval({
        "tool": "find_similar_routing_decisions",
        "query_title": title,
        "excluded_urls": sorted(excluded),
        "rows_skipped": skipped,
        "returned": [
            {
                "issue_url": example["issue_url"],
                "decision": example["decision"],
                "destination_repo": example["destination_repo"],
                "similarity": example["similarity"],
            }
            for decision in results
            for example in results[decision]
        ],
    })

    return results


@function_tool
def find_similar_routing_decisions(
    title: str,
    body: str,
    issue_url: str,
) -> dict:
    """
    Search historical repository-routing decisions for
    issues originating in getsentry/sentry.

    Call this when deciding whether an issue should
    remain in getsentry/sentry or be transferred.

    Returns similar STAY and TRANSFER examples.

    STAY examples are provisional and unverified.
    TRANSFER examples are confirmed GitHub transfers.

    Similarity scores are not routing confidence.
    An irrelevant match must not influence the decision.

    If the destination isn't represented in historical
    data, do not force a different destination.

    Pass the current issue URL so the issue can be
    excluded from its own historical matches.
    """
    return search_routing_examples(
        title=title,
        body=body,
        limit_per_decision=3,
        exclude_issue_url=issue_url,
    )


def main():
    print("Sentry Repository Routing Search")
    print("--------------------------------")

    title = input("\nIssue title: ").strip()
    body = input("Issue description: ").strip()

    if not title and not body:
        print("Please provide a title or description.")
        return

    print("\nSearching historical routing examples...\n")

    results = search_routing_examples(
        title=title,
        body=body,
    )

    for decision in ("STAY", "TRANSFER"):
        print("=" * 60)
        print(f"{decision} examples")
        print("=" * 60)

        for example in results[decision]:
            print(
                f"\nSimilarity: {example['similarity']:.3f}"
            )
            print("Title:", example["title"])
            print("Decision:", example["decision"])
            print(
                "Destination:",
                example["destination_repo"] or "None",
            )
            print("Evidence:", example["evidence_status"])
            print("URL:", example["issue_url"])
            print("Excerpt:", example["excerpt"][:350])

        print()


if __name__ == "__main__":
    main()
