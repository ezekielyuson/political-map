"""Orchestrator: fetch -> parse -> graph upsert for one FEC entity type.

Called by the CLI. Tracks a per-entity ``last_indexed`` cursor in the
``ingest_state`` table so re-runs only pull new/changed rows.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pge.graph.db import GraphDB
from pge.sources.fec import fetch, to_graph
from pge.sources.fec.parse import (
    FECCandidateRaw,
    FECCommitteeRaw,
    FECContributionRaw,
)

EntityType = Literal["committees", "candidates", "contributions"]

_STATE_KEY = {
    "committees": "fec.committees.min_last_f1_date",
    "candidates": "fec.candidates.min_last_f2_date",
    "contributions": "fec.contributions.min_date",
}


def _resolve_since(db: GraphDB, entity: EntityType, since: date | None) -> str | None:
    """CLI ``--since`` wins; otherwise fall back to the saved cursor."""
    if since is not None:
        return since.isoformat()
    return db.get_state(_STATE_KEY[entity])


def _save_cursor(db: GraphDB, entity: EntityType, value: date | None) -> None:
    if value is None:
        return
    db.set_state(_STATE_KEY[entity], value.isoformat())


def ingest(
    db: GraphDB,
    *,
    entity: EntityType,
    since: date | None = None,
    cycle: int | None = None,
    committee_id: str | None = None,
    api_key: str | None = None,
    raw_root: Path = fetch.DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> dict[str, int]:
    """Run one ingest pass. Returns ``{'rows': N}`` summary."""
    api_key = api_key or fetch.get_api_key()
    since_str = _resolve_since(db, entity, since)
    rows = 0
    max_seen: date | None = None

    if entity == "committees":
        for raw_row in fetch.iter_committees(
            api_key=api_key,
            min_last_indexed=since_str,
            cycle=cycle,
            raw_root=raw_root,
            archive=archive,
            max_pages=max_pages,
        ):
            parsed = FECCommitteeRaw.model_validate(raw_row)
            to_graph.write_committee(db, parsed)
            rows += 1
            if parsed.last_file_date and (max_seen is None or parsed.last_file_date > max_seen):
                max_seen = parsed.last_file_date

    elif entity == "candidates":
        for raw_row in fetch.iter_candidates(
            api_key=api_key,
            min_last_indexed=since_str,
            cycle=cycle,
            raw_root=raw_root,
            archive=archive,
            max_pages=max_pages,
        ):
            parsed = FECCandidateRaw.model_validate(raw_row)
            to_graph.write_candidate(db, parsed)
            rows += 1

    elif entity == "contributions":
        for raw_row in fetch.iter_contributions(
            api_key=api_key,
            min_date=since_str,
            committee_id=committee_id,
            raw_root=raw_root,
            archive=archive,
            max_pages=max_pages,
        ):
            parsed = FECContributionRaw.model_validate(raw_row)
            to_graph.write_contribution(db, parsed)
            rows += 1
            if parsed.contribution_receipt_date and (
                max_seen is None or parsed.contribution_receipt_date > max_seen
            ):
                max_seen = parsed.contribution_receipt_date

    else:
        raise ValueError(f"unknown entity: {entity}")

    _save_cursor(db, entity, max_seen)
    return {"rows": rows}
