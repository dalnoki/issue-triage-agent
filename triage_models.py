
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr


# Where the evidence behind a STAY recommendation is
# weak, shown to the reviewer. product_area_ambiguous
# and conflicting_evidence also trigger the code
# lookup (see main.needs_code_lookup).
Uncertainty = Literal[
    "ownership_disputed",
    "conflicting_evidence",
    "product_area_ambiguous",
    "weak_product_area_evidence",
]


class TriageResult(BaseModel):
    decision: Literal[
        "STAY",
        "TRANSFER",
        "REVIEW",
        "NEEDS_MORE_INFORMATION",
    ]

    suggested_repository: str | None = None
    product_area: str | None = None

    reason: str
    routing_history_summary: str
    historical_evidence: str

    supporting_issue_urls: list[str] = Field(
        default_factory=list
    )

    suspected_cause: str | None = None

    suspected_code_locations: list[str] = Field(
        default_factory=list
    )

    next_action: str

    uncertainties: list[Uncertainty] = Field(
        default_factory=list
    )

    # Set by the triage code, not the agent: whether the
    # conditional code lookup pass ran.
    _code_lookup_ran: bool = PrivateAttr(default=False)


class CodeFindings(BaseModel):
    """Output of the conditional code lookup pass."""

    suspected_cause: str | None = None

    suspected_code_locations: list[str] = Field(
        default_factory=list
    )

    next_action: str

    product_area: str | None = None
