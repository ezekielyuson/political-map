"""Pydantic models for Congress.gov v3 API responses.

Lenient (``extra='ignore'``) so new fields don't break ingest. We only model
the fields we use; everything else falls through.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class _Raw(BaseModel):
    model_config = ConfigDict(extra="ignore")


# -- /member (list) ---------------------------------------------------------


class MemberSummary(_Raw):
    """Row from ``/member`` (the list endpoint)."""

    bioguideId: str
    name: str
    state: str | None = None
    district: int | str | None = None
    partyName: str | None = None
    terms: dict | None = None  # {"item": [{"chamber": "House of Representatives", ...}]}


# -- /member/{bioguideId} (detail) -----------------------------------------


class MemberTerm(_Raw):
    chamber: str | None = None
    congress: int | None = None
    startYear: int | None = None
    endYear: int | None = None
    partyName: str | None = None
    stateCode: str | None = None
    district: int | str | None = None


class PartyHistoryEntry(_Raw):
    partyName: str
    partyAbbreviation: str | None = None
    startYear: int | None = None
    endYear: int | None = None


class MemberDetail(_Raw):
    bioguideId: str
    directOrderName: str | None = None
    firstName: str | None = None
    lastName: str | None = None
    middleName: str | None = None
    birthYear: str | None = None
    state: str | None = None
    district: int | str | None = None
    partyHistory: list[PartyHistoryEntry] = Field(default_factory=list)
    terms: list[MemberTerm] = Field(default_factory=list)
    officialWebsiteUrl: str | None = None


# -- /committee (list) -----------------------------------------------------


class CommitteeSummary(_Raw):
    """Row from ``/committee``. ``systemCode`` is the stable id we key on."""

    systemCode: str
    name: str
    chamber: str | None = None  # "House", "Senate", "Joint", "NoChamber"
    committeeTypeCode: str | None = None  # "Standing", "Select", "Joint", "Subcommittee"
    parent: dict | None = None  # for subcommittees


# -- /committee/{chamber}/{systemCode} (detail) ----------------------------


class CommitteeMemberRef(_Raw):
    bioguideId: str
    name: str | None = None
    party: str | None = None
    state: str | None = None
    title: str | None = None  # "Chair", "Ranking Member", etc.
    rank: int | None = None


class SubcommitteeRef(_Raw):
    systemCode: str
    name: str | None = None


class CommitteeDetail(_Raw):
    systemCode: str
    name: str
    chamber: str | None = None
    committeeTypeCode: str | None = None
    parent: dict | None = None
    subcommittees: list[SubcommitteeRef] = Field(default_factory=list)
    # Congress.gov member roster lives under ``currentMembers`` in newer
    # responses; older snapshots may use ``members``. Tolerate both.
    currentMembers: list[CommitteeMemberRef] = Field(default_factory=list)
    members: list[CommitteeMemberRef] = Field(default_factory=list)
    updateDate: date | None = None

    def all_members(self) -> list[CommitteeMemberRef]:
        return self.currentMembers or self.members
