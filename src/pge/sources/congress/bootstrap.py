"""Network-only seeding of Politician nodes from congress-legislators YAML.

Complements the API-driven ``--entity members`` ingest with a path that
needs **no API key**. Used by:

* the Docker build to bake a populated DB into the deploy image, so a
  fresh deploy serves real data without any post-deploy step;
* CI runs that just want a baseline graph;
* anyone who wants to demo the API without registering for FEC/Congress
  keys.

The resulting nodes only have what's in the YAML: bioguide id, name, and
the latest term's state/chamber/party. No committee assignments, no
sponsorship edges, no donations -- those still require the API or a full
local ingest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pge.graph.db import GraphDB
from pge.graph.ingest import upsert_node
from pge.schema.nodes import PoliticianNode
from pge.seed.locations import lookup as state_capital_lookup

_PARTY_MAP: dict[str, str] = {
    "Democrat": "DEM",
    "Republican": "REP",
    "Independent": "IND",
    "Libertarian": "LIB",
    "Green": "GRN",
}

_CHAMBER_MAP: dict[str, str] = {
    "sen": "senate",
    "rep": "house",
}


def _load_yaml(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as f:
        return yaml.safe_load(f)


def bootstrap_politicians_from_yaml(db: GraphDB, *paths: Path) -> dict[str, int]:
    """Seed PoliticianNodes from one or more legislators YAML files.

    For each record we use the *latest* term to derive state / chamber /
    party. Re-runs are idempotent (upsert by id).

    We deliberately do **not** stamp FEC ids into ``external_ids`` here --
    that would create a (fec, <id>) row pointing at the bioguide-keyed node,
    and a later FEC ingest would PK-conflict trying to register the same
    pair. The merge that the Phase 2 entity-resolution path does (move
    external ids to canonical at merge time) is the right place for FEC ids
    to land.
    """
    written = 0
    for p in paths:
        for record in _load_yaml(p):
            id_block = record.get("id") or {}
            bioguide = id_block.get("bioguide")
            if not bioguide:
                continue

            name = record.get("name") or {}
            first = (name.get("first") or "").strip()
            last = (name.get("last") or "").strip()
            full_name = f"{first} {last}".strip() or bioguide

            terms = record.get("terms") or []
            latest = terms[-1] if terms else {}
            chamber = _CHAMBER_MAP.get(latest.get("type") or "")
            party_raw = latest.get("party") or ""
            party = _PARTY_MAP.get(party_raw, party_raw or None)
            state = latest.get("state")
            district_raw = latest.get("district")
            district = str(district_raw) if district_raw is not None else None

            # Coarse geocoding: state capital. House district centroids
            # would be more accurate; v2 enhancement.
            coords = state_capital_lookup(state)
            lat = coords[0] if coords else None
            lng = coords[1] if coords else None

            node = PoliticianNode(
                id=f"pol:{bioguide}",
                name=full_name,
                external_ids={"congress": bioguide},
                bioguide_id=bioguide,
                state=state,
                chamber=chamber,
                party=party,
                latitude=lat,
                longitude=lng,
                district=district,
            )
            upsert_node(db, node)
            written += 1
    return {"members": written}
