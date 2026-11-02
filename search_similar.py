
import json
import math
import re
import sqlite3

from contextvars import ContextVar

from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI
from agents import function_tool

from retrieval_audit import record_retrieval


# --------------------------------------------------
# Configuration
# --------------------------------------------------

DATABASE = "issues.db"
EMBEDDING_MODEL = "text-embedding-3-small"

client = OpenAI()


# --------------------------------------------------
# Current issue context
# --------------------------------------------------

# Holds the issue number currently being triaged.
#
# This allows historical searches to exclude
# the same issue automatically.
#
# ContextVar keeps the value local to the current
# execution context rather than using a global
# variable shared by every triage request.

CURRENT_ISSUE_NUMBER = ContextVar(
    "current_issue_number",
    default=None,
)


# --------------------------------------------------
# Embeddings
# --------------------------------------------------

def create_embedding(text: str) -> list[float]:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )

    return response.data[0].embedding


# --------------------------------------------------
# Cosine similarity
# --------------------------------------------------

def cosine_similarity(
    a: list[float],
    b: list[float],
) -> float:
    dot_product = sum(
        x * y for x, y in zip(a, b)
    )

    magnitude_a = math.sqrt(
        sum(x * x for x in a)
    )

    magnitude_b = math.sqrt(
        sum(x * x for x in b)
    )

    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0

    return dot_product / (magnitude_a * magnitude_b)


# --------------------------------------------------
# Re-ranking configuration
# --------------------------------------------------

# Embeddings alone match issues about the same TOPIC
# (e.g. "native crash"), not the same FAILURE MODE
# (e.g. "public share link reloads after a 401").
#
# Retrieval therefore runs in two steps:
#
# 1. Shortlist the closest issues by embedding.
# 2. Re-rank the shortlist by how many of the
#    agent-supplied symptom terms appear in each
#    issue's title or body, plus a small recency
#    nudge.

SHORTLIST_SIZE = 40
SYMPTOM_WEIGHT = 0.15
RECENCY_WEIGHT = 0.02
EXCERPT_LENGTH = 200


def symptom_matches(
    symptoms: list[str],
    title: str | None,
    body: str | None,
) -> list[str]:
    """
    Case-insensitive match at the start of a word, so
    "share" matches "shared" and "not logged in"
    matches "not logged into", but "401" does not
    match inside "14012".
    """
    text = f"{title or ''}\n{body or ''}"

    return [
        symptom
        for symptom in symptoms
        if re.search(
            rf"(?<!\w){re.escape(symptom)}",
            text,
            re.IGNORECASE,
        )
    ]


def age_bucket(
    number: int,
    min_number: int,
    max_number: int,
) -> str:
    """
    Issue numbers are a proxy for filing date.

    "recent" is the newest 10% of the indexed number
    range, "old" is anything older than the newest 40%.
    """
    span = max(max_number - min_number, 1)
    position = (number - min_number) / span

    if position >= 0.9:
        return "recent"

    if position >= 0.6:
        return "mid"

    return "old"


def make_excerpt(body: str | None) -> str:
    text = " ".join((body or "").split())

    if len(text) <= EXCERPT_LENGTH:
        return text

    return text[:EXCERPT_LENGTH] + "..."


# --------------------------------------------------
# Search historical Product Area issues
# --------------------------------------------------

def search_similar_issues(
    query: str,
    symptoms: list[str] | None = None,
    limit: int = 5,
    exclude_issue_number: int | None = None,
) -> list[dict]:
    """
    Find historical issues similar to the query.

    When called during agent triage, automatically
    excludes the issue currently being classified.

    This prevents an agent from seeing the correct
    label of the same issue in historical data.
    """

    if exclude_issue_number is None:
        exclude_issue_number = CURRENT_ISSUE_NUMBER.get()

    if not query.strip():
        return []

    limit = max(1, min(limit, 10))

    symptoms = [
        symptom.strip()
        for symptom in (symptoms or [])
        if symptom and symptom.strip()
    ][:10]

    query_embedding = create_embedding(query)

    conn = sqlite3.connect(DATABASE)

    try:
        min_number, max_number = conn.execute("""
            SELECT MIN(number), MAX(number)
            FROM issues
            WHERE embedding IS NOT NULL
        """).fetchone()

        cursor = conn.execute("""
            SELECT
                number,
                title,
                body,
                state,
                product_area,
                url,
                embedding
            FROM issues
            WHERE embedding IS NOT NULL
              AND (? IS NULL OR number != ?)
        """, (exclude_issue_number, exclude_issue_number))

        rows = cursor.fetchall()

    finally:
        conn.close()

    candidates = []

    for (
        number,
        title,
        body,
        state,
        product_area,
        url,
        embedding_json,
    ) in rows:

        historical_embedding = json.loads(
            embedding_json
        )

        similarity = cosine_similarity(
            query_embedding,
            historical_embedding,
        )

        candidates.append((
            similarity,
            number,
            title,
            body,
            state,
            product_area,
            url,
        ))

    candidates.sort(reverse=True)
    shortlist = candidates[:SHORTLIST_SIZE]

    results = []

    for (
        similarity,
        number,
        title,
        body,
        state,
        product_area,
        url,
    ) in shortlist:

        matched = symptom_matches(symptoms, title, body)

        symptom_score = (
            len(matched) / len(symptoms)
            if symptoms else 0.0
        )

        recency = (number - min_number) / max(
            max_number - min_number, 1
        )

        score = (
            similarity
            + SYMPTOM_WEIGHT * symptom_score
            + RECENCY_WEIGHT * recency
        )

        results.append({
            "number": number,
            "title": title,
            "state": state,
            "age": age_bucket(number, min_number, max_number),
            "product_area": product_area,
            "url": url,
            "similarity": round(similarity, 3),
            "matched_symptoms": matched,
            "score": round(score, 3),
            "excerpt": make_excerpt(body),
        })

    results.sort(
        key=lambda issue: issue["score"],
        reverse=True,
    )

    results = results[:limit]

    record_retrieval({
        "tool": "find_similar_issues",
        "query": query[:300],
        "symptoms": symptoms,
        "excluded_issue_number": exclude_issue_number,
        "returned": [
            {
                "url": issue["url"],
                "product_area": issue["product_area"],
                "similarity": issue["similarity"],
                "matched_symptoms": issue["matched_symptoms"],
                "score": issue["score"],
            }
            for issue in results
        ],
    })

    return results


# --------------------------------------------------
# Agent tool
# --------------------------------------------------

@function_tool
def find_similar_issues(
    query: str,
    symptoms: list[str] | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Find historical Sentry GitHub issues similar to
    the current issue.

    Use this tool when recommending a Product Area.

    query: describe the FAILURE MODE in plain words:
    what the user did, what went wrong, and what they
    observed. Do not just name the data type or
    platform involved.

    symptoms: short, literal terms that a report of
    the same failure would likely contain, e.g.
    ["public share", "not logged in", "401", "reload"].
    Terms match at the start of a word ("reload" also
    matches "reloads"). Issues containing more of them
    rank higher.

    Returns per issue:
    - number, title, URL
    - state (open or closed)
    - age (recent, mid, or old, by issue number)
    - historical Product Area label
    - similarity (embedding relatedness)
    - matched_symptoms (which symptom terms appear)
    - score (combined ranking score)
    - excerpt (start of the issue body)

    The current issue is automatically excluded
    from historical results during triage.

    Historical labels are useful evidence, but
    they are not necessarily ground truth.

    Scores measure relatedness, not classification
    confidence.
    """

    return search_similar_issues(
        query=query,
        symptoms=symptoms,
        limit=limit,
    )


# --------------------------------------------------
# Terminal testing
# --------------------------------------------------

def main():
    print("Sentry Product Area Semantic Search")
    print("----------------------------------")

    query = input(
        "\nDescribe the GitHub issue: "
    ).strip()

    if not query:
        print("Please enter a description.")
        return

    symptoms = input(
        "Symptom terms (comma-separated, optional): "
    ).split(",")

    print("\nSearching historical issues...\n")

    results = search_similar_issues(
        query=query,
        symptoms=symptoms,
        limit=5,
    )

    for issue in results:
        print(
            f"Similarity: {issue['similarity']:.3f}"
        )
        print(
            f"#{issue['number']} — {issue['title']}"
        )
        print(
            f"Label: {issue['product_area']}"
        )
        print(
            f"State: {issue['state']}, age: {issue['age']}, "
            f"matched: {issue['matched_symptoms']}"
        )
        print(
            f"URL: {issue['url']}"
        )
        print()


if __name__ == "__main__":
    main()
