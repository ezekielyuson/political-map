"""Orchestrator: fetch -> parse -> graph upsert for one Congress.gov entity.

Three entity types exposed:

* ``members``     -- pulls /member list, then /member/{bioguide} detail per row.
                     Triggers entity resolution against any FEC-created
                     politicians.
* ``committees``  -- pulls /committee list, then /committee/{chamber}/{code}
                     for each, writing committee/subcommittee nodes and
                     CommitteeMembership edges.
* ``resolve``     -- no-fetch pass: re-runs bioguide<->fec merging against the
                     legislators YAML (catches FEC ingests that ran *after*
                     the most recent Congress ingest).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pge.graph.aliases import find_node_by_external_id, merge_nodes
from pge.graph.db import GraphDB
from pge.sources.congress import fetch
from pge.sources.congress.parse import (
    CommitteeDetail,
    CommitteeSummary,
    MemberDetail,
    MemberSummary,
)
from pge.sources.congress.resolve import LegislatorIndex, load_index
from pge.sources.congress.to_graph import (
    write_committee_detail,
    write_committee_summary,
    write_member,
)

EntityType = Literal["members", "committees", "resolve"]


def ingest_members(
    db: GraphDB,
    *,
    api_key: str,
    index: LegislatorIndex | None = None,
    raw_root: Path = fetch.DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
    current_only: bool = True,
) -> dict[str, int]:
    rows = 0
    merges = 0
    for summary in fetch.iter_members(
        api_key=api_key, current_only=current_only,
        raw_root=raw_root, archive=archive, max_pages=max_pages,
    ):
        parsed_summary = MemberSummary.model_validate(summary)
        detail_payload = fetch.get_member_detail(
            parsed_summary.bioguideId, api_key=api_key,
            raw_root=raw_root, archive=archive,
        )
        # Detail document wraps the member under either "member" or "members".
        # Tolerate both API shape variants.
        member_doc = detail_payload.get("member") or detail_payload.get("members") or detail_payload
        if isinstance(member_doc, list):
            member_doc = member_doc[0] if member_doc else {}
        detail = MemberDetail.model_validate(member_doc)
        report = write_member(db, detail, index=index)
        merges += len(report["merged_from"])
        rows += 1
    return {"members": rows, "merges": merges}


def ingest_committees(
    db: GraphDB,
    *,
    api_key: str,
    raw_root: Path = fetch.DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> dict[str, int]:
    committees = 0
    assignments = 0
    for raw_summary in fetch.iter_committees(
        api_key=api_key, raw_root=raw_root, archive=archive, max_pages=max_pages
    ):
        summary = CommitteeSummary.model_validate(raw_summary)
        # Write the summary first so committees that lack a detail still land.
        write_committee_summary(db, summary)
        committees += 1
        # Detail endpoint requires chamber slug; Congress.gov uses lowercase.
        chamber_slug = (summary.chamber or "").lower()
        if not chamber_slug or chamber_slug == "nochamber":
            continue
        detail_payload = fetch.get_committee_detail(
            chamber_slug, summary.systemCode, api_key=api_key,
            raw_root=raw_root, archive=archive,
        )
        # Detail shape: {"committee": {...}}
        committee_doc = detail_payload.get("committee") or detail_payload
        detail = CommitteeDetail.model_validate(committee_doc)
        assignments += write_committee_detail(db, detail)
    return {"committees": committees, "assignments": assignments}


def resolve_pass(
    db: GraphDB,
    *,
    index: LegislatorIndex,
) -> dict[str, int]:
    """Idempotent merge pass: union FEC-keyed politicians into bioguide-keyed.

    Used after an FEC ingest that landed *after* a Congress ingest -- which
    would otherwise leave behind an unresolved ``pol:<fec_id>`` node.
    """
    merges = 0
    for bioguide, fec_ids in index.bioguide_to_fec.items():
        canonical_id = f"pol:{bioguide}"
        canonical_exists = (
            db.conn.execute("SELECT 1 FROM nodes WHERE id = ?", (canonical_id,)).fetchone()
            is not None
        )
        if not canonical_exists:
            # No bioguide-keyed node to merge into; nothing to do here.
            continue
        for fec_id in fec_ids:
            existing = find_node_by_external_id(db, "fec", fec_id)
            if existing and existing != canonical_id:
                merge_nodes(
                    db,
                    from_id=existing,
                    to_id=canonical_id,
                    source="congress-legislators",
                )
                merges += 1
    return {"merges": merges}


def ingest(
    db: GraphDB,
    *,
    entity: EntityType,
    api_key: str | None = None,
    legislators_cache_dir: Path | None = None,
    include_historical: bool = False,
    raw_root: Path = fetch.DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
    current_only: bool = True,
) -> dict[str, int]:
    """Top-level entrypoint for the CLI."""
    if entity == "resolve":
        index = load_index(
            legislators_cache_dir or Path("data/ref"),
            include_historical=include_historical,
        )
        return resolve_pass(db, index=index)

    api_key = api_key or fetch.get_api_key()
    index = load_index(
        legislators_cache_dir or Path("data/ref"),
        include_historical=include_historical,
    )

    if entity == "members":
        return ingest_members(
            db, api_key=api_key, index=index,
            raw_root=raw_root, archive=archive,
            max_pages=max_pages, current_only=current_only,
        )
    if entity == "committees":
        return ingest_committees(
            db, api_key=api_key, raw_root=raw_root,
            archive=archive, max_pages=max_pages,
        )
    raise ValueError(f"unknown entity: {entity}")
