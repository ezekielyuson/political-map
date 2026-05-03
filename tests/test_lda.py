"""LDA source tests: parse fixture, write to graph, verify shape."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import get_edge_payload, get_node_payload
from pge.schema.edges import LobbyingContractEdge, edge_from_row
from pge.schema.nodes import CompanyNode, LobbyingFirmNode, node_from_row
from pge.sources.lda.fetch import normalize_period
from pge.sources.lda.parse import LDAFiling
from pge.sources.lda.to_graph import (
    _money_to_cents,
    _quarter_label,
    client_to_node,
    filing_to_edge,
    registrant_to_node,
    write_filing,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lda"


def _filings() -> list[dict]:
    return json.loads((FIXTURES / "filings_page1.json").read_text())["results"]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pge.db"
    init_db(path)
    return path


# ---- helpers ------------------------------------------------------------


def test_money_to_cents() -> None:
    assert _money_to_cents("120000.00") == 12_000_000
    assert _money_to_cents("0.50") == 50
    assert _money_to_cents(None) is None
    assert _money_to_cents("") is None
    assert _money_to_cents("garbage") is None
    assert _money_to_cents("-50.00") == 0  # clamped


def test_quarter_label() -> None:
    assert _quarter_label(2024, "second_quarter") == "2024Q2"
    assert _quarter_label(2024, "year_end") == "2024H2"
    assert _quarter_label(2024, None) is None
    assert _quarter_label(2024, "totally_made_up") is None


def test_normalize_period() -> None:
    assert normalize_period("q2") == "second_quarter"
    assert normalize_period("Q3") == "third_quarter"
    assert normalize_period("first_quarter") == "first_quarter"
    assert normalize_period("h1") == "mid_year"


# ---- parse + map --------------------------------------------------------


def test_parse_full_filing() -> None:
    filing = LDAFiling.model_validate(_filings()[0])
    assert filing.filing_uuid == "11111111-aaaa-bbbb-cccc-222222222222"
    assert filing.filing_year == 2024
    assert filing.filing_period == "second_quarter"
    assert filing.income == "120000.00"
    assert len(filing.lobbying_activities) == 2
    assert filing.lobbying_activities[0].general_issue_code == "TAX"


def test_parse_registration_with_no_activities() -> None:
    filing = LDAFiling.model_validate(_filings()[1])
    assert filing.income is None
    assert filing.expenses is None
    assert filing.lobbying_activities == []


def test_parse_ignores_unknown_fields() -> None:
    raw = dict(_filings()[0])
    raw["some_brand_new_field_2026"] = {"x": 1}
    filing = LDAFiling.model_validate(raw)
    assert filing.filing_uuid == "11111111-aaaa-bbbb-cccc-222222222222"


def test_registrant_to_node_shape() -> None:
    filing = LDAFiling.model_validate(_filings()[0])
    node = registrant_to_node(filing.registrant)
    assert isinstance(node, LobbyingFirmNode)
    assert node.id == "lf:lda:5001"
    assert node.lda_registrant_id == "5001"
    assert node.external_ids == {"lda": "5001"}


def test_client_to_node_shape() -> None:
    filing = LDAFiling.model_validate(_filings()[0])
    node = client_to_node(filing.client)
    assert isinstance(node, CompanyNode)
    assert node.id == "co:lda:9100"
    assert node.external_ids == {"lda": "9100"}


def test_filing_to_edge_shape_and_amounts() -> None:
    filing = LDAFiling.model_validate(_filings()[0])
    edge = filing_to_edge(filing)
    assert isinstance(edge, LobbyingContractEdge)
    assert edge.id == "lda:filing:11111111-aaaa-bbbb-cccc-222222222222"
    assert edge.src_id == "co:lda:9100"  # client -> firm
    assert edge.dst_id == "lf:lda:5001"
    assert edge.evidence_type == "VERIFIED"
    assert edge.source_name == "lda"
    assert edge.amount_cents == 12_000_000  # $120k
    assert edge.quarter == "2024Q2"
    assert edge.issue_codes == ["TAX", "ENV"]
    assert edge.as_of_date == date(2024, 7, 20)


def test_filing_with_no_income_uses_expenses() -> None:
    raw = dict(_filings()[0])
    raw["income"] = None
    raw["expenses"] = "75000.00"
    filing = LDAFiling.model_validate(raw)
    edge = filing_to_edge(filing)
    assert edge.amount_cents == 7_500_000


def test_registration_filing_has_null_amount() -> None:
    filing = LDAFiling.model_validate(_filings()[1])
    edge = filing_to_edge(filing)
    assert edge.amount_cents is None
    assert edge.issue_codes == []


# ---- graph write --------------------------------------------------------


def test_write_filing_roundtrip(db_path: Path) -> None:
    filing = LDAFiling.model_validate(_filings()[0])
    with GraphDB.open(db_path) as db:
        write_filing(db, filing)
    with GraphDB.open(db_path) as db:
        kind, payload = get_node_payload(db, "lf:lda:5001")
        firm = node_from_row(kind, payload)
        kind, payload = get_node_payload(db, "co:lda:9100")
        company = node_from_row(kind, payload)
        kind, payload = get_edge_payload(
            db, "lda:filing:11111111-aaaa-bbbb-cccc-222222222222"
        )
        edge = edge_from_row(kind, payload)
    assert isinstance(firm, LobbyingFirmNode)
    assert isinstance(company, CompanyNode)
    assert isinstance(edge, LobbyingContractEdge)
    assert edge.issue_codes == ["TAX", "ENV"]


def test_write_filing_idempotent(db_path: Path) -> None:
    filing = LDAFiling.model_validate(_filings()[0])
    with GraphDB.open(db_path) as db:
        write_filing(db, filing)
        write_filing(db, filing)
        write_filing(db, filing)
    with GraphDB.open(db_path) as db:
        stats = db.stats()
    assert stats["nodes_total"] == 2  # firm + client
    assert stats["edges_total"] == 1
    assert stats["edges_by_kind"] == {"LobbyingContract": 1}
    assert stats["edges_by_evidence"] == {"VERIFIED": 1}


def test_write_two_filings_same_firm_different_clients(db_path: Path) -> None:
    """Same registrant, two clients -> 1 firm + 2 client nodes + 2 edges."""
    raws = _filings()
    f1 = LDAFiling.model_validate(raws[0])
    f2 = LDAFiling.model_validate(raws[1])
    with GraphDB.open(db_path) as db:
        write_filing(db, f1)
        write_filing(db, f2)
    with GraphDB.open(db_path) as db:
        stats = db.stats()
    assert stats["nodes_by_kind"]["LobbyingFirm"] == 1
    assert stats["nodes_by_kind"]["Company"] == 2
    assert stats["edges_by_kind"]["LobbyingContract"] == 2


def test_lda_cursor_state_roundtrip(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        db.set_state("lda.filings.dt_posted_after", "2024-07-20")
    with GraphDB.open(db_path) as db:
        assert db.get_state("lda.filings.dt_posted_after") == "2024-07-20"
