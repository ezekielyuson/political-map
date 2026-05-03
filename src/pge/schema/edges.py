"""Edge schemas.

Every edge carries provenance (``evidence_type``, ``source_id``), epistemic
status (``strength``, ``confidence``), temporal scope (``as_of_date``, with
optional ``valid_until``), and direction (``src_id`` -> ``dst_id``).

Edges are typed via the ``kind`` discriminator. Subclass ``attrs``-style
fields hold edge-kind-specific structured data (e.g. donation amount).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceType = Literal["VERIFIED", "REPORTED", "INFERRED"]
Strength = Literal["strong", "medium", "weak"]
Confidence = Literal["high", "medium", "low"]

EVIDENCE_TYPES: tuple[EvidenceType, ...] = ("VERIFIED", "REPORTED", "INFERRED")

EdgeKind = Literal[
    "PartyAffiliation",
    "CommitteeMembership",
    "Donation",
    "IndependentExpenditure",
    "LobbyingContract",
    "LobbyingTarget",
    "VotingAlignment",
    "BillSponsorship",
    "BillVote",
    "Employment",
    "BoardMembership",
    "OwnershipStake",
    "FamilyRelation",
    "BusinessPartnership",
]


class _EdgeBase(BaseModel):
    """Common fields on every edge.

    ``id`` is caller-provided and must be unique. We recommend deriving it
    deterministically from ``(source, source_id)`` or
    ``(kind, src_id, dst_id, as_of_date)`` so that re-ingest is idempotent.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    id: str
    src_id: str
    dst_id: str
    evidence_type: EvidenceType
    source_id: str = Field(..., description="Pointer back to raw source record.")
    source_name: str = Field(..., description="Short source identifier, e.g. 'fec'.")
    strength: Strength = "medium"
    confidence: Confidence = "medium"
    as_of_date: date | None = None
    valid_until: date | None = None
    notes: str | None = None


class PartyAffiliationEdge(_EdgeBase):
    """Politician -> PoliticalParty."""

    kind: Literal["PartyAffiliation"] = "PartyAffiliation"


class CommitteeMembershipEdge(_EdgeBase):
    """Politician -> GovernmentBody."""

    kind: Literal["CommitteeMembership"] = "CommitteeMembership"
    role: str | None = Field(None, description="e.g. 'chair', 'ranking', 'member'.")


class DonationEdge(_EdgeBase):
    """Donor (Individual | PAC | Company) -> recipient (Politician | PAC).

    Currency is USD unless ``currency`` is set.
    """

    kind: Literal["Donation"] = "Donation"
    amount_cents: int = Field(..., ge=0, description="Stored as integer cents.")
    currency: str = "USD"
    cycle: int | None = Field(None, description="Election cycle year, e.g. 2024.")


class IndependentExpenditureEdge(_EdgeBase):
    """PAC -> Politician (support or oppose).

    Independent expenditures are not coordinated with the candidate, so this
    is a distinct edge type from Donation.
    """

    kind: Literal["IndependentExpenditure"] = "IndependentExpenditure"
    amount_cents: int = Field(..., ge=0)
    currency: str = "USD"
    support_oppose: Literal["support", "oppose"]


class LobbyingContractEdge(_EdgeBase):
    """Client (Company) -> LobbyingFirm."""

    kind: Literal["LobbyingContract"] = "LobbyingContract"
    amount_cents: int | None = Field(None, ge=0)
    currency: str = "USD"
    quarter: str | None = Field(None, description="e.g. '2024Q3'.")
    issue_codes: list[str] = Field(default_factory=list)


class LobbyingTargetEdge(_EdgeBase):
    """LobbyingContract -> GovernmentBody (or Politician).

    ``src_id`` references the contract edge id; ``dst_id`` is the target body.
    Lets us trace which agencies/committees a given contract touched.
    """

    kind: Literal["LobbyingTarget"] = "LobbyingTarget"


class VotingAlignmentEdge(_EdgeBase):
    """Politician -> Politician (INFERRED from co-vote patterns)."""

    kind: Literal["VotingAlignment"] = "VotingAlignment"
    alignment_score: float = Field(..., ge=0.0, le=1.0)
    sample_size: int = Field(..., ge=0)
    window_start: date | None = None
    window_end: date | None = None


class BillSponsorshipEdge(_EdgeBase):
    """Politician -> Bill."""

    kind: Literal["BillSponsorship"] = "BillSponsorship"
    role: Literal["sponsor", "cosponsor"]


class BillVoteEdge(_EdgeBase):
    """Politician -> Bill."""

    kind: Literal["BillVote"] = "BillVote"
    position: Literal["yea", "nay", "present", "not_voting"]
    roll_call: str | None = None


class EmploymentEdge(_EdgeBase):
    """Individual -> Company (or LobbyingFirm)."""

    kind: Literal["Employment"] = "Employment"
    title: str | None = None


class BoardMembershipEdge(_EdgeBase):
    """Individual -> Company."""

    kind: Literal["BoardMembership"] = "BoardMembership"
    title: str | None = "director"


class OwnershipStakeEdge(_EdgeBase):
    """Owner (Individual | Company) -> Company."""

    kind: Literal["OwnershipStake"] = "OwnershipStake"
    pct: float | None = Field(None, ge=0.0, le=100.0)
    shares: int | None = Field(None, ge=0)
    filing_type: str | None = Field(None, description="e.g. '13D', '13G', 'proxy'.")


class FamilyRelationEdge(_EdgeBase):
    """Individual -> Individual."""

    kind: Literal["FamilyRelation"] = "FamilyRelation"
    relation: Literal["spouse", "child", "parent", "sibling", "in_law", "other"]


class BusinessPartnershipEdge(_EdgeBase):
    """Company -> Company (joint venture, parent/sub, etc.)."""

    kind: Literal["BusinessPartnership"] = "BusinessPartnership"
    relation: Literal["subsidiary", "parent", "joint_venture", "partner", "other"]


Edge = Annotated[
    PartyAffiliationEdge
    | CommitteeMembershipEdge
    | DonationEdge
    | IndependentExpenditureEdge
    | LobbyingContractEdge
    | LobbyingTargetEdge
    | VotingAlignmentEdge
    | BillSponsorshipEdge
    | BillVoteEdge
    | EmploymentEdge
    | BoardMembershipEdge
    | OwnershipStakeEdge
    | FamilyRelationEdge
    | BusinessPartnershipEdge,
    Field(discriminator="kind"),
]


_EDGE_KIND_TO_CLS: dict[str, type[_EdgeBase]] = {
    "PartyAffiliation": PartyAffiliationEdge,
    "CommitteeMembership": CommitteeMembershipEdge,
    "Donation": DonationEdge,
    "IndependentExpenditure": IndependentExpenditureEdge,
    "LobbyingContract": LobbyingContractEdge,
    "LobbyingTarget": LobbyingTargetEdge,
    "VotingAlignment": VotingAlignmentEdge,
    "BillSponsorship": BillSponsorshipEdge,
    "BillVote": BillVoteEdge,
    "Employment": EmploymentEdge,
    "BoardMembership": BoardMembershipEdge,
    "OwnershipStake": OwnershipStakeEdge,
    "FamilyRelation": FamilyRelationEdge,
    "BusinessPartnership": BusinessPartnershipEdge,
}


def edge_from_row(kind: str, payload: dict) -> _EdgeBase:
    """Reconstruct a typed edge from a DB row's kind + JSON payload."""
    cls = _EDGE_KIND_TO_CLS.get(kind)
    if cls is None:
        raise ValueError(f"unknown edge kind: {kind}")
    return cls.model_validate(payload)
