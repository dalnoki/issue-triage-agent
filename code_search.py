
import os
import requests

from contextvars import ContextVar
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()

from agents import function_tool

from retrieval_audit import record_retrieval


# --------------------------------------------------
# Configuration
# --------------------------------------------------

# Read-only lookups in the getsentry/sentry source
# code, so the agent can point to a likely code
# location instead of restating the symptom.
#
# The code is the CURRENT default branch. It may
# differ from the version the reporter runs
# (especially on self-hosted).

CODE_REPO = "getsentry/sentry"
GITHUB_API = "https://api.github.com"

MAX_SEARCH_RESULTS = 5
MAX_FRAGMENT_LENGTH = 200
MAX_FILE_LINES = 60
MAX_CODE_TOOL_CALLS = 3


# --------------------------------------------------
# Per-triage call budget
# --------------------------------------------------

# Set to a fresh list at the start of each triage run.
# Each code tool call appends to it; once the budget
# is used up, the tools return an error instead of
# calling GitHub. Defaults to None (no budget), e.g.
# for direct calls from a terminal.

CODE_TOOL_CALLS = ContextVar(
    "code_tool_calls",
    default=None,
)


def budget_exhausted(tool: str) -> bool:
    calls = CODE_TOOL_CALLS.get()

    if calls is None:
        return False

    if len(calls) >= MAX_CODE_TOOL_CALLS:
        return True

    calls.append(tool)
    return False


BUDGET_ERROR = (
    f"Code tool budget of {MAX_CODE_TOOL_CALLS} calls "
    "used up. Finish with the evidence you have."
)


def github_headers(accept: str) -> dict:
    token = os.getenv("GITHUB_TOKEN")

    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is missing from .env"
        )

    return {
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# --------------------------------------------------
# Code search
# --------------------------------------------------

def search_code(
    query: str,
    path: str | None = None,
    limit: int = MAX_SEARCH_RESULTS,
) -> dict:
    """
    Search getsentry/sentry code with GitHub code
    search.

    Errors (e.g. the code search rate limit of about
    10 requests per minute) are returned, not raised,
    so the agent can continue without code evidence.
    """
    query = query.strip()

    if not query:
        return {"error": "Empty query.", "results": []}

    limit = max(1, min(limit, MAX_SEARCH_RESULTS))

    qualifiers = f"repo:{CODE_REPO}"

    if path:
        qualifiers += f" path:{path.strip()}"

    response = requests.get(
        f"{GITHUB_API}/search/code",
        headers=github_headers(
            "application/vnd.github.text-match+json"
        ),
        params={
            "q": f"{query} {qualifiers}",
            "per_page": limit,
        },
        timeout=25,
    )

    if response.status_code != 200:
        result = {
            "error": (
                f"GitHub code search failed with "
                f"{response.status_code}: "
                f"{response.text[:200]}"
            ),
            "results": [],
        }

    else:
        items = response.json().get("items", [])

        result = {
            "total_count": response.json().get(
                "total_count", 0
            ),
            "results": [
                {
                    "path": item["path"],
                    "url": item["html_url"],
                    "fragments": [
                        match["fragment"][:MAX_FRAGMENT_LENGTH]
                        for match in item.get(
                            "text_matches", []
                        )
                    ][:2],
                }
                for item in items
            ],
        }

    record_retrieval({
        "tool": "search_sentry_code",
        "query": query[:300],
        "path": path,
        "returned": [
            item["path"] for item in result["results"]
        ],
        "error": result.get("error"),
    })

    return result


@function_tool
def search_sentry_code(
    query: str,
    path: str | None = None,
) -> dict:
    """
    Search the getsentry/sentry source code.

    Use literal strings from the report that would
    appear in code: API endpoint paths (e.g.
    "/committers/"), error messages, UI routes,
    component or function names.

    query: one to four literal terms. GitHub code
    search matches all terms; long natural-language
    queries usually return nothing.

    path: optional directory to narrow results, e.g.
    "static/app" (frontend) or "src/sentry" (backend).

    Returns matching file paths, GitHub URLs, and
    short matching code fragments.

    The code is the current default branch, not
    necessarily the reporter's version.
    """
    if budget_exhausted("search_sentry_code"):
        return {"error": BUDGET_ERROR, "results": []}

    return search_code(query, path)


# --------------------------------------------------
# Read a file
# --------------------------------------------------

@lru_cache(maxsize=64)
def fetch_file_lines(path: str) -> list[str]:
    response = requests.get(
        f"{GITHUB_API}/repos/{CODE_REPO}/contents/"
        f"{path.strip().lstrip('/')}",
        headers=github_headers(
            "application/vnd.github.raw+json"
        ),
        timeout=25,
    )

    response.raise_for_status()

    return response.text.splitlines()


def read_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict:
    try:
        lines = fetch_file_lines(path)
    except requests.HTTPError as error:
        return {
            "error": (
                f"Could not read {path}: "
                f"{error.response.status_code}"
            ),
        }

    start_line = max(1, start_line)

    if end_line is None:
        end_line = start_line + MAX_FILE_LINES - 1

    end_line = min(
        end_line,
        start_line + MAX_FILE_LINES - 1,
        len(lines),
    )

    record_retrieval({
        "tool": "read_sentry_file",
        "path": path,
        "lines": [start_line, end_line],
    })

    return {
        "path": path,
        "url": (
            f"https://github.com/{CODE_REPO}/blob/master/"
            f"{path.strip().lstrip('/')}"
            f"#L{start_line}-L{end_line}"
        ),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": end_line,
        "content": "\n".join(
            f"{number}: {line}"
            for number, line in enumerate(
                lines[start_line - 1:end_line],
                start=start_line,
            )
        ),
    }


@function_tool
def read_sentry_file(
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict:
    """
    Read lines from a getsentry/sentry source file,
    usually one found with search_sentry_code.

    Returns at most 60 numbered lines, plus a GitHub
    URL pointing at exactly those lines. Cite that
    URL as a suspected code location.
    """
    if budget_exhausted("read_sentry_file"):
        return {"error": BUDGET_ERROR}

    return read_file(path, start_line, end_line)
