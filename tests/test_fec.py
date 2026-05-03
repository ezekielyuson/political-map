"""FEC source tests: parse fixtures, write to graph, verify shape."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import get_edge_payload, get_node_payload
from pge.schema.edges import DonationEdge, edge_from_row
from pge.schema.nodes import IndividualNode, PACNode, PoliticianNode, node_from_row
from pge.sources.fec.parse import (
    FECCandidateRaw,
    FECCommitteeRaw,
    FECContributionRaw,
)
from pge.sources.fec.to_graph import (
    candidate_to_node,
    committee_to_node,
    contribution_to_edge,
    individual_id,
    write_candidate,
    write_committee,
    write_contribution,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fec"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())["results"]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pge.db"
    init_db(path)
    return path


# ----- parse layer ---------------------------------------------------------

def test_parse_committees() -> None:
    rows = _load("committees_page1.json")
    parsed = [FECCommitteeRaw.model_validate(r) for r in rows]
    assert len(parsed) == 2
    assert parsed[0].committee_id == "C00123456"
    assert parsed[0].committee_type == "O"
    assert parsed[1].cycles == [2024]


def test_parse_candidates() -> None:
    rows = _load("candidates_page1.json")
    parsed = [FECCandidateRaw.model_validate(r) for r in rows]
    assert parsed[0].office == "H"
    assert parsed[1].office == "S"
    assert parsed[1].district is None


def test_parse_contributions() -> None:
    rows = _load("schedule_a_page1.json")
    parsed = [FECContributionRaw.model_validate(r) for r in rows]
    assert len(parsed) == 3
    assert parsed[0].is_individual is True
    assert parsed[1].contributor_id == "C00123456"
    assert parsed[2].contribution_receipt_amount == 100.00


def test_parse_ignores_unknown_fields() -> None:
    """FEC adds fields routinely; we must not crash on them."""
    raw = {
        "committee_id": "C00000001",
        "name": "TEST",
        "some_new_field_FEC_added_yesterday": {"nested": "junk"},
    }
    parsed = FECCommitteeRaw.model_validate(raw)
    assert parsed.committee_id == "C00000001"


# ----- mapping layer -------------------------------------------------------

def test_committee_maps_to_pac_node() -> None:
    raw = FECCommitteeRaw.model_validate(_load("committees_page1.json")[0])
    node = committee_to_node(raw)
    assert node.id == "pac:C00123456"
    assert node.pac_type == "super"  # committee_type 'O'
    assert node.external_ids == {"fec": "C00123456"}


def test_candidate_maps_to_politician() -> None:
    raw = FECCandidateRaw.model_validate(_load("candidates_page1.json")[0])
    node = candidate_to_node(raw)
    assert node.id == "pol:H8CA17123"
    assert node.chamber == "house"
    assert node.party == "DEM"
    assert node.fec_candidate_id == "H8CA17123"


def test_individual_id_is_deterministic() -> None:
    a = individual_id("Jane Doe", "Acme Corp", "94025", "CA")
    b = individual_id("JANE DOE", "acme corp", "94025-1234", "CA")
    c = individual_id("Jane Doe", "Different Co", "94025", "CA")
    assert a == b  # normalized + zip5 match
    assert a != c  # different employer -> different id


def test_contribution_individual_donor_creates_individual_node() -> None:
    raw = FECContributionRaw.model_validate(_load("schedule_a_page1.json")[0])
    src_node, edge = contribution_to_edge(raw)
    assert isinstance(src_node, IndividualNode)
    assert edge.amount_cents == 25000  # $250.00
    assert edge.evidence_type == "VERIFIED"
    assert edge.source_name == "fec"
    assert edge.dst_id == "pac:C00999999"


def test_contribution_committee_donor_creates_pac_node() -> None:
    raw = FECContributionRaw.model_validate(_load("schedule_a_page1.json")[1])
    src_node, edge = contribution_to_edge(raw)
    assert isinstance(src_node, PACNode)
    assert src_node.id == "pac:C00123456"
    assert edge.src_id == "pac:C00123456"
    assert edge.amount_cents == 500000  # $5000.00


# ----- graph write layer ---------------------------------------------------

def test_write_committee_roundtrip(db_path: Path) -> None:
    raw = FECCommitteeRaw.model_validate(_load("committees_page1.json")[0])
    with GraphDB.open(db_path) as db:
        write_committee(db, raw)
    with GraphDB.open(db_path) as db:
        kind, payload = get_node_payload(db, "pac:C00123456")
    node = node_from_row(kind, payload)
    assert isinstance(node, PACNode)
    assert node.fec_committee_id == "C00123456"


def test_write_candidate_roundtrip(db_path: Path) -> None:
    raw = FECCandidateRaw.model_validate(_load("candidates_page1.json")[0])
    with GraphDB.open(db_path) as db:
        write_candidate(db, raw)
    with GraphDB.open(db_path) as db:
        kind, payload = get_node_payload(db, "pol:H8CA17123")
    node = node_from_row(kind, payload)
    assert isinstance(node, PoliticianNode)
    assert node.chamber == "house"


def test_write_contribution_roundtrip(db_path: Path) -> None:
    # Recipient committee must exist first (FK).
    committee_raw = FECCommitteeRaw.model_validate(_load("committees_page1.json")[1])
    contrib_raws = [FECContributionRaw.model_validate(r) for r in _load("schedule_a_page1.json")]
    with GraphDB.open(db_path) as db:
        write_committee(db, committee_raw)
        for c in contrib_raws:
            write_contribution(db, c)

    with GraphDB.open(db_path) as db:
        stats = db.stats()
        # 3 contribs, but 2 are from the same individual (deterministic id) ->
        # 1 individual node + 1 pac contributor node + 1 recipient = 3 nodes
        # plus 0 extra (recipient already counted)
        assert stats["nodes_total"] == 3
        assert stats["edges_total"] == 3
        assert stats["edges_by_evidence"] == {"VERIFIED": 3}
        assert stats["edges_by_kind"] == {"Donation": 3}

        # Verify the same-individual coalescing
        kind, payload = get_edge_payload(db, "fec:contrib:4061320241499000001")
        edge1 = edge_from_row(kind, payload)
        kind, payload = get_edge_payload(db, "fec:contrib:4061320241499000003")
        edge3 = edge_from_row(kind, payload)
        assert isinstance(edge1, DonationEdge)
        assert isinstance(edge3, DonationEdge)
        assert edge1.src_id == edge3.src_id  # same donor


def test_contribution_idempotent_reingest(db_path: Path) -> None:
    committee_raw = FECCommitteeRaw.model_validate(_load("committees_page1.json")[1])
    contrib_raw = FECContributionRaw.model_validate(_load("schedule_a_page1.json")[0])
    with GraphDB.open(db_path) as db:
        write_committee(db, committee_raw)
        write_contribution(db, contrib_raw)
        write_contribution(db, contrib_raw)
        write_contribution(db, contrib_raw)
    with GraphDB.open(db_path) as db:
        stats = db.stats()
    assert stats["edges_total"] == 1
    assert stats["nodes_total"] == 2  # recipient committee + individual donor


def test_state_cursor_roundtrip(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        db.set_state("fec.committees.min_last_f1_date", "2024-09-30")
    with GraphDB.open(db_path) as db:
        assert db.get_state("fec.committees.min_last_f1_date") == "2024-09-30"
        assert db.get_state("nope") is None


def test_contribution_amount_clamp_negative() -> None:
    """Refunds appear as negative amounts; we clamp to 0 rather than crash."""
    raw = FECContributionRaw.model_validate(
        {
            "sub_id": "neg-1",
            "committee_id": "C00999999",
            "contributor_name": "REFUND, R.",
            "contribution_receipt_amount": -50.00,
            "contribution_receipt_date": "2024-08-01",
        }
    )
    _, edge = contribution_to_edge(raw)
    assert edge.amount_cents == 0


def test_dates_use_date_type() -> None:
    """Make sure date columns survive parse + serialize."""
    raw = FECContributionRaw.model_validate(_load("schedule_a_page1.json")[0])
    _, edge = contribution_to_edge(raw)
    assert edge.as_of_date == date(2024, 6, 15)
