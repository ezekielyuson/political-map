"""Node schemas.

Every node has:
- ``id``: stable internal identifier (caller-provided; we recommend
  ``<source>:<source_id>`` for raw nodes and ``ent:<uuid>`` for resolved
  canonical entities).
- ``kind``: discriminator string matching one of :class:`NodeKind`.
- ``external_ids``: ``{source_name: source_id}`` for cross-source resolution.
- ``attrs``: kind-specific structured fields, defined by each subclass.

Subclasses override ``kind`` with a Literal so Pydantic's discriminated unions
work cleanly when we deserialize from the DB.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeKind = Literal[
    "Politician",
    "PoliticalParty",
    "GovernmentBody",
    "Company",
    "PAC",
    "LobbyingFirm",
    "Individual",
    "Bill",
]


class _NodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)

    id: str = Field(..., description="Stable internal id, unique across all nodes.")
    external_ids: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternate names / spellings; lowercase recommended.",
    )
    name: str
    notes: str | None = None


class PoliticianNode(_NodeBase):
    kind: Literal["Politician"] = "Politician"
    bioguide_id: str | None = None
    fec_candidate_id: str | None = None
    state: str | None = Field(None, description="USPS state code, e.g. 'CA'.")
    chamber: Literal["house", "senate", "executive", None] = None
    party: str | None = Field(None, description="Latest known party affiliation.")
    birth_date: date | None = None


class PoliticalPartyNode(_NodeBase):
    kind: Literal["PoliticalParty"] = "PoliticalParty"
    abbreviation: str | None = None  # e.g. "DEM", "REP", "IND"


class GovernmentBodyNode(_NodeBase):
    kind: Literal["GovernmentBody"] = "GovernmentBody"
    body_type: Literal["committee", "subcommittee", "agency", "caucus", "other"]
    chamber: Literal["house", "senate", "joint", None] = None
    parent_body_id: str | None = None


class CompanyNode(_NodeBase):
    kind: Literal["Company"] = "Company"
    cik: str | None = Field(None, description="SEC EDGAR Central Index Key.")
    ticker: str | None = None
    industry: str | None = None
    sector: str | None = None


class PACNode(_NodeBase):
    kind: Literal["PAC"] = "PAC"
    fec_committee_id: str | None = None
    pac_type: Literal["corporate", "trade", "labor", "leadership", "super", "hybrid", "other"] = (
        "other"
    )
    affiliated_org: str | None = None


class LobbyingFirmNode(_NodeBase):
    kind: Literal["LobbyingFirm"] = "LobbyingFirm"
    lda_registrant_id: str | None = None


class IndividualNode(_NodeBase):
    """Catch-all for natural persons who aren't politicians.

    Includes spouses, donors, executives, lobbyists. Entity resolution may
    later cluster these into a canonical individual.
    """

    kind: Literal["Individual"] = "Individual"
    occupation: str | None = None
    employer: str | None = None


class BillNode(_NodeBase):
    kind: Literal["Bill"] = "Bill"
    congress: int | None = None
    bill_type: str | None = Field(None, description="e.g. 'hr', 's', 'hjres'.")
    bill_number: int | None = None
    introduced_date: date | None = None


Node = Annotated[
    PoliticianNode
    | PoliticalPartyNode
    | GovernmentBodyNode
    | CompanyNode
    | PACNode
    | LobbyingFirmNode
    | IndividualNode
    | BillNode,
    Field(discriminator="kind"),
]


_NODE_KIND_TO_CLS: dict[str, type[_NodeBase]] = {
    "Politician": PoliticianNode,
    "PoliticalParty": PoliticalPartyNode,
    "GovernmentBody": GovernmentBodyNode,
    "Company": CompanyNode,
    "PAC": PACNode,
    "LobbyingFirm": LobbyingFirmNode,
    "Individual": IndividualNode,
    "Bill": BillNode,
}


def node_from_row(kind: str, payload: dict) -> _NodeBase:
    """Reconstruct a typed node from a DB row's kind + JSON payload."""
    cls = _NODE_KIND_TO_CLS.get(kind)
    if cls is None:
        raise ValueError(f"unknown node kind: {kind}")
    return cls.model_validate(payload)
