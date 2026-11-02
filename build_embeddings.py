import json
import sqlite3

from openai import OpenAI


DATABASE = "issues.db"
EMBEDDING_MODEL = "text-embedding-3-small"

client = OpenAI()


def add_embedding_column(conn):
    """
    Add an embedding column if it doesn't exist yet.
    """

    columns = conn.execute(
        "PRAGMA table_info(issues)"
    ).fetchall()

    column_names = [
        column[1]
        for column in columns
    ]

    if "embedding" not in column_names:
        print("Adding embedding column...")

        conn.execute("""
            ALTER TABLE issues
            ADD COLUMN embedding TEXT
        """)

        conn.commit()


def get_issues_without_embeddings(conn):
    """
    Get issues that haven't been embedded yet.
    """

    cursor = conn.execute("""
        SELECT number, title, body
        FROM issues
        WHERE embedding IS NULL
    """)

    return cursor.fetchall()


def create_embedding(title, body):
    """
    Turn the issue's title + body into a vector.
    """

    text = f"""
Title:
{title}

Body:
{body}
""".strip()

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )

    return response.data[0].embedding


def save_embedding(conn, issue_number, embedding):
    """
    Store the vector as JSON inside SQLite.
    """

    conn.execute(
        """
        UPDATE issues
        SET embedding = ?
        WHERE number = ?
        """,
        (
            json.dumps(embedding),
            issue_number,
        ),
    )


def main():
    conn = sqlite3.connect(DATABASE)

    add_embedding_column(conn)

    issues = get_issues_without_embeddings(conn)

    print(f"Need to embed {len(issues)} issues.")

    for index, (number, title, body) in enumerate(issues, start=1):

        print(
            f"[{index}/{len(issues)}] "
            f"Embedding #{number}: {title[:60]}"
        )

        try:
            embedding = create_embedding(
                title,
                body,
            )

            save_embedding(
                conn,
                number,
                embedding,
            )

            # Save after every issue so progress isn't lost.
            conn.commit()

        except Exception as e:
            print(
                f"ERROR embedding issue #{number}: {e}"
            )

    conn.close()

    print("\nFinished building embeddings.")


if __name__ == "__main__":
    main()