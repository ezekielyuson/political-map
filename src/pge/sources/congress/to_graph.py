"""Map Congress.gov rows to graph nodes/edges.

Entity resolution at write time
-------------------------------
A member's canonical politician id is ``pol:<bioguideId>``. The FEC ingest
(Phase 1) creates politicians keyed ``pol:<fec_candidate_id>`` because FEC
doesn't know about bioguides. Here we close the loop:

1. Look up FEC ids for this bioguide via :class:`LegislatorIndex`.
2. For each FEC id, check whether ``pol:<fec_id>`` already exists in the DB.
3. Upsert the bioguide-keyed canonical node.
4. Merge each existing FEC-keyed node *into* the canonical node, recording
   the alias for backwards lookups and rewriting incident edges.

Resolution is explicit (caller passes the index in) and idempotent (re-running
yields the same end state).
"""

from __future__ import annotations

from pge.graph.aliases import find_node_by_external_id, merge_nodes
from pge.graph.db import GraphDB
from pge.graph.ingest import upsert_edge, upsert_node
from pge.schema.edges import CommitteeMembershipEdge
from pge.schema.nodes import GovernmentBodyNode, PoliticianNode
from pge.sources.congress.parse import (
    CommitteeDetail,
    CommitteeMemberRef,
    CommitteeSummary,
    MemberDetail,
)
from pge.sources.congress.resolve import LegislatorIndex

SOURCE_NAME = "congress"

# Congress.gov chamber strings -> our vocabulary.
_CHAMBER_MAP = {
    "House of Representatives": "house",
    "House": "house",
    "Senate": "senate",
    "Joint": "joint",
    "NoChamber": None,
}

_PARTY_TO_ABBR = {
    "Democratic": "DEM",
    "Republican": "REP",
    "Independent": "IND",
    "Libertarian": "LIB",
}


def _latest_term(detail: MemberDetail):
    if not detail.terms:
        return None
    # Congress.gov returns terms in chronological order; last is most recent.
    return detail.terms[-1]


def _chamber_from_detail(detail: MemberDetail) -> str | None:
    term = _latest_term(detail)
    if term and term.chamber:
        return _CHAMBER_MAP.get(term.chamber)
    return None


def _party_from_detail(detail: MemberDetail) -> str | None:
    if detail.partyHistory:
        last = detail.partyHistory[-1]
        return _PARTY_TO_ABBR.get(last.partyName, last.partyAbbreviation or last.partyName)
    term = _latest_term(detail)
    if term and term.partyName:
        return _PARTY_TO_ABBR.get(term.partyName, term.partyName)
    return None


def member_to_node(detail: MemberDetail) -> PoliticianNode:
    """Build the canonical PoliticianNode for a member.

    We deliberately do NOT pre-stamp FEC ids into ``external_ids`` -- those
    move over during :func:`merge_nodes` when the FEC-created node already
    exists, which keeps the (source, ext_id) primary key happy.
    """
    return PoliticianNode(
        id=f"pol:{detail.bioguideId}",
        name=detail.directOrderName or f"{detail.firstName or ''} {detail.lastName or ''}".strip(),
        external_ids={SOURCE_NAME: detail.bioguideId},
        bioguide_id=detail.bioguideId,
        state=detail.state,
        chamber=_chamber_from_detail(detail),
        party=_party_from_detail(detail),
    )


def committee_to_node(summary: CommitteeSummary) -> GovernmentBodyNode:
    chamber = _CHAMBER_MAP.get(summary.chamber or "") if summary.chamber else None
    is_sub = (summary.committeeTypeCode or "").lower() == "subcommittee" or summary.parent
    parent_id = None
    if summary.parent and isinstance(summary.parent, dict):
        parent_code = summary.parent.get("systemCode")
        if parent_code:
            parent_id = f"gov:{parent_code}"
    return GovernmentBodyNode(
        id=f"gov:{summary.systemCode}",
        name=summary.name,
        external_ids={SOURCE_NAME: summary.systemCode},
        body_type="subcommittee" if is_sub else "committee",
        chamber=chamber,
        parent_body_id=parent_id,
    )


def committee_detail_to_node(detail: CommitteeDetail) -> GovernmentBodyNode:
    summary = CommitteeSummary(
        systemCode=detail.systemCode,
        name=detail.name,
        chamber=detail.chamber,
        committeeTypeCode=detail.committeeTypeCode,
        parent=detail.parent,
    )
    return committee_to_node(summary)


def assignment_to_edge(
    committee: CommitteeDetail, member: CommitteeMemberRef
) -> CommitteeMembershipEdge:
    """One CommitteeMembership edge per (committee, member) pair."""
    politician_id = f"pol:{member.bioguideId}"
    body_id = f"gov:{committee.systemCode}"
    edge_id = f"congress:assign:{committee.systemCode}:{member.bioguideId}"
    return CommitteeMembershipEdge(
        id=edge_id,
        src_id=politician_id,
        dst_id=body_id,
        evidence_type="VERIFIED",
        source_name=SOURCE_NAME,
        source_id=f"{committee.systemCode}/{member.bioguideId}",
        role=(member.title or "member").lower(),
        as_of_date=committee.updateDate,
        strength="strong",
        confidence="high",
    )


# ----- write helpers (DB-touching) ----------------------------------------


def write_member(
    db: GraphDB, detail: MemberDetail, index: LegislatorIndex | None = None
) -> dict[str, object]:
    """Upsert a member and merge any existing FEC-keyed duplicates.

    Returns a small report describing what happened, useful for tests and
    CLI summaries.
    """
    canonical_id = f"pol:{detail.bioguideId}"
    fec_ids: list[str] = []
    if index is not None:
        fec_ids = index.fec_ids_for_bioguide(detail.bioguideId)

    # Find FEC-created nodes that should be merged into the canonical one.
    to_merge: list[str] = []
    for fec_id in fec_ids:
        existing = find_node_by_external_id(db, "fec", fec_id)
        if existing and existing != canonical_id and existing not in to_merge:
            to_merge.append(existing)

    upsert_node(db, member_to_node(detail))

    for from_id in to_merge:
        merge_nodes(
            db,
            from_id=from_id,
            to_id=canonical_id,
            source="congress-legislators",
            confidence="high",
        )

    return {
        "id": canonical_id,
        "fec_ids": fec_ids,
        "merged_from": to_merge,
    }


def write_committee_summary(db: GraphDB, summary: CommitteeSummary) -> None:
    upsert_node(db, committee_to_node(summary))


def write_committee_detail(db: GraphDB, detail: CommitteeDetail) -> int:
    """Upsert the committee node and one edge per current member.

    Returns the number of assignment edges written.
    """
    upsert_node(db, committee_detail_to_node(detail))
    written = 0
    for member in detail.all_members():
        edge = assignment_to_edge(detail, member)
        upsert_edge(db, edge)
        written += 1
    return written
