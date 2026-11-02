
from contextvars import ContextVar


# --------------------------------------------------
# Retrieval audit context
# --------------------------------------------------

# These ContextVars let an evaluation run control and
# observe RAG retrieval without changing the agent's
# tool signatures.
#
# EXCLUDED_ROUTING_URLS:
#     Routing-example URLs that must never be returned,
#     regardless of what URL the agent passes to the
#     tool. Used for holdout evaluations, where the
#     agent must not see the issue's own transfer.
#
# RETRIEVAL_LOG:
#     When set to a list, every search appends a record
#     of what it excluded and what it returned. This is
#     how we verify, from inside the tool, that the
#     exclusions actually propagated through the Agents
#     SDK tool-execution machinery.
#
# Both default to "off", so the normal live triage
# flow behaves exactly as before.

EXCLUDED_ROUTING_URLS = ContextVar(
    "excluded_routing_urls",
    default=frozenset(),
)

RETRIEVAL_LOG = ContextVar(
    "retrieval_log",
    default=None,
)


def normalize_issue_url(url: str | None) -> str | None:
    """
    Normalize a GitHub issue URL for comparison.

    Lowercases, drops query strings, fragments, and
    trailing slashes, so that the same issue always
    compares equal.
    """
    if not url:
        return None

    url = url.strip().split("#", 1)[0].split("?", 1)[0]

    return url.rstrip("/").lower()


def record_retrieval(entry: dict) -> None:
    log = RETRIEVAL_LOG.get()

    if log is not None:
        log.append(entry)
