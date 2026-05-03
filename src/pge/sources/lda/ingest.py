"""Orchestrator: fetch LDA filings -> parse -> graph upsert.

Tracks an incremental ``dt_posted_after`` cursor in ``ingest_state`` so
re-runs only pull newly-posted filings (the LDA API exposes ``dt_posted_after``
as a query filter).
"""

from __future__ import annotations

from pathlib import Path

from pge.graph.db import GraphDB
from pge.sources.lda import fetch
from pge.sources.lda.parse import LDAFiling
from pge.sources.lda.to_graph import write_filing

_CURSOR_KEY = "lda.filings.dt_posted_after"


def _resolve_cursor(db: GraphDB, dt_posted_after: str | None) -> str | None:
    if dt_posted_after is not None:
        return dt_posted_after
    return db.get_state(_CURSOR_KEY)


def _save_cursor(db: GraphDB, value: str | None) -> None:
    if value:
        db.set_state(_CURSOR_KEY, value)


def ingest(
    db: GraphDB,
    *,
    filing_year: int | None = None,
    filing_period: str | None = None,
    dt_posted_after: str | None = None,
    api_key: str | None = None,
    raw_root: Path = fetch.DEFAULT_RAW_ROOT,
    archive: bool = True,
    max_pages: int | None = None,
) -> dict[str, int]:
    """Run one ingest pass. Returns ``{'filings': N}``."""
    api_key = api_key if api_key is not None else fetch.get_api_key()
    cursor = _resolve_cursor(db, dt_posted_after)

    rows = 0
    latest_seen: str | None = None

    for raw in fetch.iter_filings(
        api_key=api_key,
        filing_year=filing_year,
        filing_period=filing_period,
        dt_posted_after=cursor,
        raw_root=raw_root,
        archive=archive,
        max_pages=max_pages,
    ):
        filing = LDAFiling.model_validate(raw)
        write_filing(db, filing)
        rows += 1
        if filing.dt_posted is not None:
            iso = filing.dt_posted.date().isoformat()
            if latest_seen is None or iso > latest_seen:
                latest_seen = iso

    _save_cursor(db, latest_seen)
    return {"filings": rows}
