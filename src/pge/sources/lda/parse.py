"""Pydantic models for LDA API filing responses.

LDA returns rich nested objects per filing. We model the fields we use and
let everything else fall through (``extra='ignore'``) so new fields don't
break ingest.

Reference: https://lda.senate.gov/api/redoc/ -- see the ``Filing`` schema.

The income/expenses fields arrive as decimal strings ("2500.00") or null.
We coerce in the mapper, not in the model, to keep the parse layer dumb.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LDARegistrant(_Raw):
    id: int
    name: str
    description: str | None = None
    address_1: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None


class LDAClient(_Raw):
    id: int
    name: str
    general_description: str | None = None
    state: str | None = None
    country: str | None = None
    state_or_local_government: str | None = None  # "Yes"/"No"; rare
    client_government_entity: bool | None = None


class LDALobbyist(_Raw):
    """One row in ``lobbying_activities[].lobbyists``.

    The lobbyist's name is the only field always present; everything else
    (covered_position, lobbyist.id) varies by filing version.
    """

    lobbyist: dict | None = None
    covered_position: str | None = None
    new: bool | None = None


class LDALobbyingActivity(_Raw):
    """One activity within a filing -- has its own issue code and lobbyist set."""

    general_issue_code: str | None = None  # e.g. "TAX", "ENV", "BUD"
    general_issue_code_display: str | None = None
    description: str | None = None
    lobbyists: list[LDALobbyist] = Field(default_factory=list)
    government_entities: list[dict] = Field(default_factory=list)
    foreign_entity_issues: str | None = None


class LDAFiling(_Raw):
    """One LDA filing (LD-1, LD-2, or LD-203)."""

    filing_uuid: str
    filing_type: str | None = Field(
        None, description="e.g. 'FIRST_QUARTER_REPORT', 'REGISTRATION'."
    )
    filing_type_display: str | None = None
    filing_year: int
    filing_period: str | None = Field(
        None, description="e.g. 'first_quarter', 'mid_year'."
    )
    filing_period_display: str | None = None
    filing_document_url: str | None = None
    income: str | None = None  # decimal-as-string from the API
    expenses: str | None = None
    expenses_method: str | None = None
    posted_by_name: str | None = None
    dt_posted: datetime | None = None
    registrant: LDARegistrant
    client: LDAClient
    lobbying_activities: list[LDALobbyingActivity] = Field(default_factory=list)
    termination_date: date | None = None
