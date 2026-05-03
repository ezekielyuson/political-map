"""Bioguide <-> FEC mapping from `unitedstates/congress-legislators`.

Congress.gov's API does not expose FEC candidate IDs on member records. The
canonical bridge is the public ``unitedstates/congress-legislators`` repo,
which publishes ``legislators-current.yaml`` and ``legislators-historical.yaml``
containing per-legislator id mappings:

    - id:
        bioguide: L000174
        fec:
        - S0VT00033
        - S6VT00065
        opensecrets: N00009918
        ...

A single bioguide can map to multiple FEC ids (one per candidacy). All of
them belong to the same person, so all of them resolve to the same canonical
politician node.

Why not match on (name, state, chamber)? Fuzzy matching is a Phase 5 problem
-- congress-legislators gives us a curated, high-confidence mapping and we
should use it instead of inventing one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

LEGISLATORS_CURRENT_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
    "main/legislators-current.yaml"
)
LEGISLATORS_HISTORICAL_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators/"
    "main/legislators-historical.yaml"
)

DEFAULT_CACHE_DIR = Path("data/ref")


@dataclass
class LegislatorIndex:
    """In-memory bidirectional id index built from one or more YAML files."""

    bioguide_to_fec: dict[str, list[str]] = field(default_factory=dict)
    fec_to_bioguide: dict[str, str] = field(default_factory=dict)
    bioguide_to_other: dict[str, dict[str, Any]] = field(default_factory=dict)

    def fec_ids_for_bioguide(self, bioguide: str) -> list[str]:
        return list(self.bioguide_to_fec.get(bioguide, ()))

    def bioguide_for_fec(self, fec_id: str) -> str | None:
        return self.fec_to_bioguide.get(fec_id)

    def other_ids(self, bioguide: str) -> dict[str, Any]:
        """Returns the full id block for a bioguide (opensecrets, govtrack, etc.)."""
        return dict(self.bioguide_to_other.get(bioguide, {}))


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as f:
        return yaml.safe_load(f)


def build_index_from_files(*paths: Path) -> LegislatorIndex:
    """Build a :class:`LegislatorIndex` from one or more YAML files."""
    idx = LegislatorIndex()
    for p in paths:
        for record in _load_yaml(p):
            id_block = record.get("id") or {}
            bioguide = id_block.get("bioguide")
            if not bioguide:
                continue
            fec_ids = id_block.get("fec") or []
            if isinstance(fec_ids, str):  # tolerant: occasionally a single string
                fec_ids = [fec_ids]
            if fec_ids:
                # First-seen wins for fec->bioguide; bioguide->[fec] is a list union.
                existing = idx.bioguide_to_fec.setdefault(bioguide, [])
                for fid in fec_ids:
                    if fid not in existing:
                        existing.append(fid)
                    idx.fec_to_bioguide.setdefault(fid, bioguide)
            # Stash everything else for downstream (opensecrets, govtrack, etc.)
            others = {k: v for k, v in id_block.items() if k not in {"bioguide", "fec"}}
            if others:
                idx.bioguide_to_other.setdefault(bioguide, {}).update(others)
    return idx


def _download(url: str, dest: Path, client: httpx.Client | None = None) -> Path:
    own = client is None
    client = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest
    finally:
        if own:
            client.close()


def ensure_legislators_yaml(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    *,
    include_historical: bool = False,
    force_refresh: bool = False,
) -> list[Path]:
    """Download the YAML(s) if missing. Returns the list of file paths."""
    paths: list[Path] = []
    current = cache_dir / "legislators-current.yaml"
    if force_refresh or not current.exists():
        _download(LEGISLATORS_CURRENT_URL, current)
    paths.append(current)
    if include_historical:
        historical = cache_dir / "legislators-historical.yaml"
        if force_refresh or not historical.exists():
            _download(LEGISLATORS_HISTORICAL_URL, historical)
        paths.append(historical)
    return paths


def load_index(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    *,
    include_historical: bool = False,
    force_refresh: bool = False,
) -> LegislatorIndex:
    """One-call helper: ensure files exist, then build an index."""
    paths = ensure_legislators_yaml(
        cache_dir, include_historical=include_historical, force_refresh=force_refresh
    )
    return build_index_from_files(*paths)
