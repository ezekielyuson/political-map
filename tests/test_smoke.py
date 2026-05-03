"""Smoke test: round-trip a node and an edge through the SQLite layer."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import (
    get_edge_payload,
    get_node_payload,
    upsert_edge,
    upsert_node,
)
from pge.schema.edges import DonationEdge, edge_from_row
from pge.schema.nodes import PACNode, PoliticianNode, node_from_row


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pge.db"
    init_db(path)
    return path


def test_init_creates_schema(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        tables = {
            row["name"]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"nodes", "edges", "external_ids", "meta"} <= tables


def test_node_edge_roundtrip(db_path: Path) -> None:
    politician = PoliticianNode(
        id="pol:H8CA17123",
        name="Jane Doe",
        external_ids={"fec": "H8CA17123", "bioguide": "D000123"},
        state="CA",
        chamber="house",
        party="DEM",
    )
    pac = PACNode(
        id="pac:C00123456",
        name="Acme Action Fund",
        external_ids={"fec": "C00123456"},
        fec_committee_id="C00123456",
        pac_type="corporate",
    )
    donation = DonationEdge(
        id="fec:contrib:abc-123",
        src_id=pac.id,
        dst_id=politician.id,
        evidence_type="VERIFIED",
        source_name="fec",
        source_id="abc-123",
        amount_cents=250000,
        cycle=2024,
        as_of_date=date(2024, 6, 15),
        strength="strong",
        confidence="high",
    )

    with GraphDB.open(db_path) as db:
        upsert_node(db, politician)
        upsert_node(db, pac)
        upsert_edge(db, donation)

    with GraphDB.open(db_path) as db:
        kind, payload = get_node_payload(db, politician.id)
        recovered_pol = node_from_row(kind, payload)
        kind, payload = get_node_payload(db, pac.id)
        recovered_pac = node_from_row(kind, payload)
        kind, payload = get_edge_payload(db, donation.id)
        recovered_edge = edge_from_row(kind, payload)

    assert isinstance(recovered_pol, PoliticianNode)
    assert recovered_pol == politician
    assert isinstance(recovered_pac, PACNode)
    assert recovered_pac == pac
    assert isinstance(recovered_edge, DonationEdge)
    assert recovered_edge == donation


def test_upsert_is_idempotent(db_path: Path) -> None:
    politician = PoliticianNode(id="pol:1", name="Same Person", state="NY")
    with GraphDB.open(db_path) as db:
        upsert_node(db, politician)
        upsert_node(db, politician)
        upsert_node(db, politician)
        n = db.conn.execute("SELECT COUNT(*) FROM nodes WHERE id = ?", (politician.id,)).fetchone()[
            0
        ]
    assert n == 1


def test_external_ids_resolution(db_path: Path) -> None:
    politician = PoliticianNode(
        id="pol:1",
        name="Cross Sourced",
        external_ids={"fec": "H1", "bioguide": "B1"},
    )
    with GraphDB.open(db_path) as db:
        upsert_node(db, politician)
        row = db.conn.execute(
            "SELECT node_id FROM external_ids WHERE source = ? AND ext_id = ?",
            ("bioguide", "B1"),
        ).fetchone()
    assert row["node_id"] == "pol:1"


def test_stats_reports_counts(db_path: Path) -> None:
    politician = PoliticianNode(id="pol:1", name="A")
    pac = PACNode(id="pac:1", name="B")
    donation = DonationEdge(
        id="d:1",
        src_id=pac.id,
        dst_id=politician.id,
        evidence_type="VERIFIED",
        source_name="fec",
        source_id="x",
        amount_cents=100,
    )
    with GraphDB.open(db_path) as db:
        upsert_node(db, politician)
        upsert_node(db, pac)
        upsert_edge(db, donation)
    with GraphDB.open(db_path) as db:
        stats = db.stats()
    assert stats["nodes_total"] == 2
    assert stats["edges_total"] == 1
    assert stats["nodes_by_kind"] == {"Politician": 1, "PAC": 1}
    assert stats["edges_by_kind"] == {"Donation": 1}
    assert stats["edges_by_evidence"] == {"VERIFIED": 1}
