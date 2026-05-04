"""Tests for the seed module + the FEC bulk-pas2 ingest path."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import upsert_node
from pge.schema.nodes import PoliticianNode
from pge.seed.companies import (
    CURATED_COMPANIES,
    find_for_organization,
)
from pge.seed.locations import STATE_CAPITALS, lookup
from pge.sources.congress.bootstrap import bootstrap_politicians_from_yaml
from pge.sources.congress.resolve import build_index_from_files
from pge.sources.fec.bulk import (
    CM_COLS,
    PAS2_COLS,
    build_pac_company_map,
    ingest_from_local_zips,
    write_company_and_pacs,
    write_donations,
)

CONGRESS_FIXTURES = Path(__file__).parent / "fixtures" / "congress"


# ---- seed module --------------------------------------------------------


def test_state_capitals_cover_50_plus_dc() -> None:
    # 50 states + DC at minimum.
    assert len(STATE_CAPITALS) >= 51


def test_state_capital_lookup() -> None:
    coords = lookup("CA")
    assert coords is not None
    lat, lng = coords
    # Sacramento, give it a generous 1 degree box.
    assert 37.5 < lat < 39.5
    assert -122.5 < lng < -120.5


def test_state_capital_lookup_unknown() -> None:
    assert lookup("ZZ") is None
    assert lookup(None) is None
    assert lookup("") is None


def test_curated_company_aliases_unique_enough() -> None:
    """No alias in one company should accidentally match another company's
    canonical name. Catches obvious authoring slips."""
    for company in CURATED_COMPANIES:
        for other in CURATED_COMPANIES:
            if company.name == other.name:
                continue
            for alias in company.aliases:
                # Be tolerant: "GENERAL" appears in both GE and General Dynamics
                # which is exactly why we use multi-word aliases. Fail only if
                # the alias is identical to the other company's name.
                if alias.upper() == other.name.upper():
                    raise AssertionError(
                        f"alias {alias!r} on {company.name} collides with "
                        f"{other.name}'s name"
                    )


def test_find_for_organization_matches() -> None:
    assert find_for_organization("JPMORGAN CHASE & CO").name == "JPMorgan Chase"
    assert find_for_organization("BOEING COMPANY").name == "Boeing"
    assert find_for_organization("WAL-MART STORES, INC.").name == "Walmart"


def test_find_for_organization_no_match() -> None:
    assert find_for_organization("RANDOMCORP LLC") is None
    assert find_for_organization("") is None
    assert find_for_organization(None) is None


def test_curated_companies_have_valid_coordinates() -> None:
    for c in CURATED_COMPANIES:
        # US bounds, generously.
        assert 18 <= c.latitude <= 50, f"{c.name} lat {c.latitude}"
        assert -125 <= c.longitude <= -65, f"{c.name} lng {c.longitude}"


# ---- bootstrap geocoding ------------------------------------------------


def test_bootstrap_geocodes_politicians() -> None:
    """After bootstrap, every politician has lat/lng set from the state cap."""
    fixture = CONGRESS_FIXTURES / "legislators.yaml"
    db_path = Path("/tmp") / "test_bootstrap_geocode.db"
    if db_path.exists():
        db_path.unlink()
    init_db(db_path)
    with GraphDB.open(db_path) as db:
        bootstrap_politicians_from_yaml(db, fixture)
    with GraphDB.open(db_path) as db:
        rows = db.conn.execute(
            "SELECT payload FROM nodes WHERE kind='Politician'"
        ).fetchall()
    import json

    for row in rows:
        attrs = json.loads(row["payload"])
        assert attrs["latitude"] is not None, f"missing lat for {attrs['name']}"
        assert attrs["longitude"] is not None, f"missing lng for {attrs['name']}"


# ---- bulk pas2 ingest ---------------------------------------------------


def _write_pipe_zip(path: Path, member_name: str, columns: list[str], rows: list[list[str]]) -> None:
    """Helper: write a zip containing a single pipe-delimited text member."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        body_lines = []
        for r in rows:
            padded = list(r) + [""] * (len(columns) - len(r))
            body_lines.append("|".join(padded))
        zf.writestr(member_name, "\n".join(body_lines))
    path.write_bytes(buf.getvalue())


@pytest.fixture
def fake_cm_zip(tmp_path: Path) -> Path:
    """A tiny committee-master with one PAC sponsored by JPMorgan, one by
    Boeing, one by an unmatched random company."""
    p = tmp_path / "cm.zip"
    rows = [
        # CMTE_ID | CMTE_NM | TRES_NM | st1|st2|city|st|zip|dsgn|tp|pty|filing|org_tp|connected|cand
        ["C00JPM001", "JPMORGAN CHASE PAC", "T", "1", "", "NYC", "NY", "10001",
         "U", "Q", "", "M", "C", "JPMORGAN CHASE & CO", ""],
        ["C00BOE001", "BOEING COMPANY PAC", "T", "1", "", "ARL", "VA", "22202",
         "U", "Q", "", "M", "C", "THE BOEING COMPANY", ""],
        ["C00ZZZ001", "ACME WHATEVER PAC", "T", "1", "", "", "", "",
         "U", "Q", "", "M", "C", "ACME WHATEVER LLC", ""],
    ]
    _write_pipe_zip(p, "cm.txt", CM_COLS, rows)
    return p


@pytest.fixture
def fake_pas2_zip(tmp_path: Path) -> Path:
    """A tiny pas2 with: JPM->Doe, Boeing->Roe, unmatched->Doe."""
    p = tmp_path / "pas24.zip"
    rows = [
        # CMTE_ID | AMNDT | RPT | PGI | IMG | TXN_TP | ENTITY | NAME | CITY |
        # STATE | ZIP | EMP | OCC | DT | AMT | OTHER_ID | CAND_ID | TRAN_ID |
        # FILE_NUM | MEMO_CD | MEMO_TX | SUB_ID
        ["C00JPM001", "N", "12G", "P", "img1", "24K", "PAC", "DOE, JANE",
         "MENLO PARK", "CA", "94025", "", "", "06152024", "5000.00",
         "H8CA17123", "H8CA17123", "T1", "F1", "", "", "S001"],
        ["C00BOE001", "N", "12G", "P", "img1", "24K", "PAC", "ROE, RICHARD",
         "NYC", "NY", "10001", "", "", "07012024", "10000.00",
         "S4NY00042", "S4NY00042", "T2", "F1", "", "", "S002"],
        # Unmatched PAC: should be ignored.
        ["C00ZZZ001", "N", "12G", "P", "img1", "24K", "PAC", "DOE, JANE",
         "", "", "", "", "", "06152024", "1000.00",
         "H8CA17123", "H8CA17123", "T3", "F1", "", "", "S003"],
        # Zero-amount: should be skipped.
        ["C00JPM001", "N", "12G", "P", "img1", "24K", "PAC", "DOE, JANE",
         "", "", "", "", "", "06152024", "0.00",
         "H8CA17123", "H8CA17123", "T4", "F1", "", "", "S004"],
    ]
    _write_pipe_zip(p, "itpas2.txt", PAS2_COLS, rows)
    return p


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "pge.db"
    init_db(p)
    # Politicians need to exist as targets for the donations.
    with GraphDB.open(p) as db:
        upsert_node(
            db,
            PoliticianNode(
                id="pol:D000123", name="Jane Doe",
                external_ids={"congress": "D000123"},
                bioguide_id="D000123",
            ),
        )
        upsert_node(
            db,
            PoliticianNode(
                id="pol:R000999", name="Richard Roe",
                external_ids={"congress": "R000999"},
                bioguide_id="R000999",
            ),
        )
    return p


def test_build_pac_company_map_filters_to_curated(fake_cm_zip: Path) -> None:
    pac_to_company, pac_names = build_pac_company_map(fake_cm_zip)
    # Only the JPM and Boeing PACs match curated companies.
    assert set(pac_to_company) == {"C00JPM001", "C00BOE001"}
    assert pac_to_company["C00JPM001"].name == "JPMorgan Chase"
    assert pac_to_company["C00BOE001"].name == "Boeing"
    assert pac_names["C00JPM001"] == "JPMORGAN CHASE PAC"


def test_write_company_and_pacs_creates_nodes_and_edges(
    db_path: Path, fake_cm_zip: Path
) -> None:
    pac_to_company, pac_names = build_pac_company_map(fake_cm_zip)
    with GraphDB.open(db_path) as db:
        report = write_company_and_pacs(db, pac_to_company, pac_names)
    assert report["companies"] == 2
    assert report["pacs"] == 2
    assert report["affiliations"] == 2
    with GraphDB.open(db_path) as db:
        stats = db.stats()
    assert stats["nodes_by_kind"].get("Company") == 2
    assert stats["nodes_by_kind"].get("PAC") == 2
    assert stats["edges_by_kind"].get("BusinessPartnership") == 2


def test_write_donations_filters_unmatched_and_zero(
    db_path: Path, fake_cm_zip: Path, fake_pas2_zip: Path
) -> None:
    pac_to_company, pac_names = build_pac_company_map(fake_cm_zip)
    with GraphDB.open(db_path) as db:
        write_company_and_pacs(db, pac_to_company, pac_names)
    fec_to_bioguide = {"H8CA17123": "D000123", "S4NY00042": "R000999"}
    pac_ids = set(pac_to_company)
    with GraphDB.open(db_path) as db:
        n = write_donations(db, fake_pas2_zip, pac_ids, fec_to_bioguide)
    # Two real donations land; unmatched PAC + zero-amount get filtered out.
    assert n == 2
    with GraphDB.open(db_path) as db:
        rows = db.conn.execute(
            "SELECT id, src_id, dst_id FROM edges WHERE kind='Donation' "
            "ORDER BY id"
        ).fetchall()
    edge_ids = {r["id"] for r in rows}
    assert edge_ids == {"fec:contrib:S001", "fec:contrib:S002"}


def test_end_to_end_local_ingest(
    db_path: Path, fake_cm_zip: Path, fake_pas2_zip: Path
) -> None:
    """The whole bulk pipeline against a local fake-zip pair."""
    legislators_fixture = CONGRESS_FIXTURES / "legislators.yaml"
    index = build_index_from_files(legislators_fixture)
    # Override the fec_to_bioguide so the local zip's IDs match.
    index.fec_to_bioguide["H8CA17123"] = "D000123"
    index.fec_to_bioguide["S4NY00042"] = "R000999"
    with GraphDB.open(db_path) as db:
        report = ingest_from_local_zips(
            db, cm_zip=fake_cm_zip, pas2_zip=fake_pas2_zip,
            legislators_index=index,
        )
    assert report["companies"] == 2
    assert report["pacs"] == 2
    assert report["donations"] == 2

    with GraphDB.open(db_path) as db:
        stats = db.stats()
    # 2 polits + 2 companies + 2 PACs = 6 nodes, no resolution merges.
    assert stats["nodes_total"] == 6
    # 2 affil + 2 donation = 4 edges.
    assert stats["edges_total"] == 4
