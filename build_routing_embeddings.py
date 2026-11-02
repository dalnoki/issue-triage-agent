
import json
import re
import sqlite3

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DATABASE = "repo_routing.db"
EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 20


def prepare_routing_text(title, body):
    """
    Prepare issue content for semantic search.

    Remove the reporter-selected Product Area so
    it cannot bias repository routing.
    """
    body = body or ""

    # Remove the Product Area section, including
    # its value, up to the next Markdown heading.
    body = re.sub(
        r"(?im)^###\s*Product Area\s*\n"
        r".*?(?=^#{1,3}\s|\Z)",
        "",
        body,
        flags=re.DOTALL,
    )

    # Remove Jira synchronization footer.
    body = re.sub(
        r"(?m)^┆Issue is synchronized.*$",
        "",
        body,
    )

    text = f"Title: {title}\n\n{body.strip()}"

    # Keep individual inputs reasonably small.
    return text[:6000]


def main():
    client = OpenAI()
    conn = sqlite3.connect(DATABASE)

    try:
        rows = conn.execute("""
            SELECT example_id, title, body
            FROM routing_examples
            WHERE embedding IS NULL
            ORDER BY example_id
        """).fetchall()

        print(f"Examples needing embeddings: {len(rows)}")

        if not rows:
            print("Nothing to do.")
            return

        completed = 0

        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]

            texts = [
                prepare_routing_text(title, body)
                for _, title, body in batch
            ]

            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
            )

            # Match each returned embedding to its input.
            vectors = {
                item.index: item.embedding
                for item in response.data
            }

            for index, (example_id, _, _) in enumerate(batch):
                conn.execute(
                    """
                    UPDATE routing_examples
                    SET embedding = ?
                    WHERE example_id = ?
                    """,
                    (
                        json.dumps(vectors[index]),
                        example_id,
                    ),
                )

            # Save after each batch, making the
            # process safe to resume.
            conn.commit()

            completed += len(batch)

            print(
                f"Embedded {completed}/{len(rows)} examples"
            )

        print("\nFinished building routing embeddings.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
