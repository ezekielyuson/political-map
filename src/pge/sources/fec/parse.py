"""Pydantic schemas for raw FEC API rows.

These models describe what FEC returns -- not what we store. They use
``extra='ignore'`` so newly-added FEC fields don't break ingestion. Mapping to
graph nodes/edges happens in :mod:`pge.sources.fec.to_graph`.

Field reference (truncated to fields we actually use):

* https://api.open.fec.gov/developers/  -- live schema browser
* Committee ``committee_id`` is a stable string like ``C00123456``.
* Candidate ``candidate_id`` is like ``H8CA17123`` (chamber/year/state/seq).
* Contribution ``sub_id`` is the unique transaction id (use as ``source_id``).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FECCommitteeRaw(_Raw):
    committee_id: str
    name: str
    committee_type: str | None = None
    committee_type_full: str | None = None
    designation: str | None = None
    designation_full: str | None = None
    organization_type: str | None = None
    organization_type_full: str | None = None
    state: str | None = None
    party: str | None = None
    treasurer_name: str | None = None
    first_file_date: date | None = None
    last_file_date: date | None = None
    cycles: list[int] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)


class FECCandidateRaw(_Raw):
    candidate_id: str
    name: str
    party: str | None = None
    party_full: str | None = None
    state: str | None = None
    district: str | None = None
    office: str | None = Field(None, description="'H', 'S', or 'P'.")
    office_full: str | None = None
    incumbent_challenge: str | None = None
    candidate_status: str | None = None
    cycles: list[int] = Field(default_factory=list)
    election_years: list[int] = Field(default_factory=list)
    principal_committees: list[dict] = Field(default_factory=list)


class FECContributionRaw(_Raw):
    """One row from ``/schedules/schedule_a/`` (itemized contribution)."""

    sub_id: str | int = Field(..., description="Stable transaction id; cast to str downstream.")
    committee_id: str | None = Field(
        None, description="Recipient committee (where the money went)."
    )
    contributor_id: str | None = Field(
        None, description="Set when contributor is itself an FEC committee."
    )
    contributor_name: str | None = None
    contributor_employer: str | None = None
    contributor_occupation: str | None = None
    contributor_state: str | None = None
    contributor_zip: str | None = None
    contributor_city: str | None = None
    entity_type: str | None = Field(
        None, description="IND=individual, COM=committee, ORG, PAC, PTY, CCM, CAN."
    )
    is_individual: bool | None = None
    contribution_receipt_amount: float | None = None
    contribution_receipt_date: date | None = None
    receipt_type: str | None = None
    receipt_type_full: str | None = None
