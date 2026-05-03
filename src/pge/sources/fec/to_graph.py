"""Map FEC raw rows into graph nodes and edges.

ID conventions
--------------
* PAC (committee) node:    ``pac:<committee_id>``           e.g. ``pac:C00123456``
* Politician node:         ``pol:<candidate_id>``           e.g. ``pol:H8CA17123``
* Individual donor node:   ``ind:fec:<hash>``               see :func:`individual_id`
* Donation edge:           ``fec:contrib:<sub_id>``

Why hash individual donor ids?
------------------------------
FEC does not assign stable ids to individual contributors. Two contributions
from "Jane Doe / Acme Corp / 90210" almost certainly represent the same
person; two from "Jane Doe / Different Employer / 12345" might or might not.
We hash a small key so re-ingest is idempotent and a single donor's repeated
contributions in the same employment context cluster naturally. Phase 5
(``dedupe``) does the real entity resolution across these proto-clusters.
"""

from __future__ import annotations

import hashlib
import re

from pge.graph.db import GraphDB
from pge.graph.ingest import upsert_edge, upsert_node
from pge.schema.edges import DonationEdge
from pge.schema.nodes import IndividualNode, PACNode, PoliticianNode
from pge.sources.fec.parse import FECCandidateRaw, FECCommitteeRaw, FECContributionRaw

SOURCE_NAME = "fec"

# Map FEC committee_type letters to our PACNode.pac_type vocabulary.
# Reference: https://www.fec.gov/campaign-finance-data/committee-type-code-descriptions/
_PAC_TYPE_MAP = {
    "C": "corporate",   # Communication cost
    "Q": "trade",       # Qualified non-party (often trade assoc.)
    "N": "trade",       # Non-qualified non-party
    "L": "labor",       # Labor / membership
    "M": "labor",
    "T": "trade",
    "V": "trade",
    "W": "trade",
    "U": "labor",
    "I": "leadership",  # Ind. expenditure (no contribution)
    "O": "super",       # Super PAC (independent-expenditure-only)
    "P": "other",       # Presidential principal
    "H": "other",       # House principal
    "S": "other",       # Senate principal
    "X": "other",
    "Y": "other",
    "Z": "other",
    "D": "other",       # Delegate committee
    "E": "other",
}

# Map FEC office letter to our chamber vocabulary.
_OFFICE_TO_CHAMBER = {"H": "house", "S": "senate", "P": "executive"}


def _normalize(s: str | None) -> str:
    """Lowercase, collapse whitespace, strip punctuation. Used for hashing."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def individual_id(
    name: str | None,
    employer: str | None,
    zip_code: str | None,
    state: str | None,
) -> str:
    """Deterministic id for an individual donor.

    Same (normalized name, employer, zip5, state) -> same id. Different keys ->
    different ids, which is the right default until entity resolution runs.
    """
    zip5 = (zip_code or "")[:5]
    key = "|".join([_normalize(name), _normalize(employer), _normalize(zip5), _normalize(state)])
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return f"ind:fec:{digest}"


def committee_to_node(raw: FECCommitteeRaw) -> PACNode:
    return PACNode(
        id=f"pac:{raw.committee_id}",
        name=raw.name,
        external_ids={SOURCE_NAME: raw.committee_id},
        fec_committee_id=raw.committee_id,
        pac_type=_PAC_TYPE_MAP.get(raw.committee_type or "", "other"),
        affiliated_org=raw.organization_type_full,
    )


def candidate_to_node(raw: FECCandidateRaw) -> PoliticianNode:
    chamber = _OFFICE_TO_CHAMBER.get(raw.office or "")
    return PoliticianNode(
        id=f"pol:{raw.candidate_id}",
        name=raw.name,
        external_ids={SOURCE_NAME: raw.candidate_id},
        fec_candidate_id=raw.candidate_id,
        state=raw.state,
        chamber=chamber,
        party=raw.party,
    )


def _contributor_node(raw: FECContributionRaw) -> tuple[str, IndividualNode | PACNode | None]:
    """Return ``(node_id, node_or_None)`` for the contributor side of a contribution.

    If the contributor is a committee we already ingested, returns its existing
    node id with ``node=None`` (no upsert needed). Otherwise mints an
    ``IndividualNode`` for the donor.
    """
    if raw.contributor_id:
        # Contributor is itself a registered FEC committee.
        node_id = f"pac:{raw.contributor_id}"
        node = PACNode(
            id=node_id,
            name=raw.contributor_name or raw.contributor_id,
            external_ids={SOURCE_NAME: raw.contributor_id},
            fec_committee_id=raw.contributor_id,
        )
        return node_id, node

    # Treat as individual donor.
    node_id = individual_id(
        raw.contributor_name,
        raw.contributor_employer,
        raw.contributor_zip,
        raw.contributor_state,
    )
    node = IndividualNode(
        id=node_id,
        name=raw.contributor_name or "(unnamed contributor)",
        occupation=raw.contributor_occupation,
        employer=raw.contributor_employer,
    )
    return node_id, node


def contribution_to_edge(raw: FECContributionRaw) -> tuple[IndividualNode | PACNode, DonationEdge]:
    """Build ``(contributor_node, donation_edge)`` from a Schedule A row.

    Recipient committee must already exist as a node (insert it first or run
    a committee ingest pass beforehand).
    """
    src_id, src_node = _contributor_node(raw)
    dst_id = f"pac:{raw.committee_id}"
    sub_id = str(raw.sub_id)
    amount_dollars = raw.contribution_receipt_amount or 0.0
    amount_cents = int(round(amount_dollars * 100))
    edge = DonationEdge(
        id=f"fec:contrib:{sub_id}",
        src_id=src_id,
        dst_id=dst_id,
        evidence_type="VERIFIED",
        source_name=SOURCE_NAME,
        source_id=sub_id,
        amount_cents=max(amount_cents, 0),  # negative refunds are uncommon; clamp + log later
        as_of_date=raw.contribution_receipt_date,
        strength="strong",
        confidence="high",
    )
    return src_node, edge


def write_committee(db: GraphDB, raw: FECCommitteeRaw) -> None:
    upsert_node(db, committee_to_node(raw))


def write_candidate(db: GraphDB, raw: FECCandidateRaw) -> None:
    upsert_node(db, candidate_to_node(raw))


def write_contribution(db: GraphDB, raw: FECContributionRaw) -> None:
    src_node, edge = contribution_to_edge(raw)
    if src_node is not None:
        upsert_node(db, src_node)
    upsert_edge(db, edge)
