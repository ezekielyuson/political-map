"""Congress.gov source + entity-resolution tests.

Network-free: every test reads from ``tests/fixtures/congress/*``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pge.graph.aliases import (
    find_node_by_external_id,
    merge_nodes,
    resolve_id,
    set_alias,
)
from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import get_node_payload, upsert_edge, upsert_node
from pge.schema.edges import CommitteeMembershipEdge, DonationEdge, edge_from_row
from pge.schema.nodes import (
    PACNode,
    PoliticianNode,
    node_from_row,
)
from pge.sources.congress.parse import (
    CommitteeDetail,
    CommitteeSummary,
    MemberDetail,
)
from pge.sources.congress.resolve import build_index_from_files
from pge.sources.congress.to_graph import (
    assignment_to_edge,
    committee_to_node,
    member_to_node,
    write_committee_detail,
    write_member,
)
from pge.sources.fec.parse import FECCandidateRaw
from pge.sources.fec.to_graph import write_candidate

FIXTURES = Path(__file__).parent / "fixtures" / "congress"
FEC_FIXTURES = Path(__file__).parent / "fixtures" / "fec"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pge.db"
    init_db(path)
    return path


@pytest.fixture
def index():
    return build_index_from_files(FIXTURES / "legislators.yaml")


# ---- legislator index ----------------------------------------------------


def test_index_bioguide_to_fec(index) -> None:
    assert index.fec_ids_for_bioguide("D000123") == ["H8CA17123", "H6CA17089"]
    assert index.fec_ids_for_bioguide("L000174") == ["S0VT00033", "S6VT00065"]
    assert index.fec_ids_for_bioguide("R000999") == ["S4NY00042"]
    assert index.fec_ids_for_bioguide("nope") == []


def test_index_fec_to_bioguide(index) -> None:
    assert index.bioguide_for_fec("H8CA17123") == "D000123"
    assert index.bioguide_for_fec("S6VT00065") == "L000174"
    assert index.bioguide_for_fec("XYZ") is None


def test_index_other_ids_preserved(index) -> None:
    others = index.other_ids("D000123")
    assert others.get("opensecrets") == "N00099001"
    assert others.get("govtrack") == 412345


# ---- parse + map ---------------------------------------------------------


def test_member_detail_parses_and_maps() -> None:
    payload = json.loads((FIXTURES / "member_detail_D000123.json").read_text())
    detail = MemberDetail.model_validate(payload["member"])
    node = member_to_node(detail)
    assert isinstance(node, PoliticianNode)
    assert node.id == "pol:D000123"
    assert node.bioguide_id == "D000123"
    assert node.chamber == "house"
    assert node.party == "DEM"
    assert node.external_ids == {"congress": "D000123"}


def test_committee_summary_maps() -> None:
    payload = json.loads((FIXTURES / "committees_list.json").read_text())
    rows = [CommitteeSummary.model_validate(r) for r in payload["committees"]]
    parent = committee_to_node(rows[0])
    sub = committee_to_node(rows[1])
    assert parent.id == "gov:hsju00"
    assert parent.body_type == "committee"
    assert parent.chamber == "house"
    assert sub.body_type == "subcommittee"
    assert sub.parent_body_id == "gov:hsju00"


def test_assignment_edge_shape() -> None:
    payload = json.loads((FIXTURES / "committee_detail_hsju00.json").read_text())
    detail = CommitteeDetail.model_validate(payload["committee"])
    member = detail.all_members()[0]
    edge = assignment_to_edge(detail, member)
    assert isinstance(edge, CommitteeMembershipEdge)
    assert edge.src_id == "pol:D000123"
    assert edge.dst_id == "gov:hsju00"
    assert edge.role == "chair"
    assert edge.evidence_type == "VERIFIED"
    assert edge.source_name == "congress"


# ---- aliases primitive ---------------------------------------------------


def test_resolve_id_walks_chain(db_path: Path) -> None:
    a = PoliticianNode(id="pol:A", name="A")
    b = PoliticianNode(id="pol:B", name="B")
    with GraphDB.open(db_path) as db:
        upsert_node(db, a)
        upsert_node(db, b)
        set_alias(db, "pol:A", "pol:B", source="t")
    with GraphDB.open(db_path) as db:
        assert resolve_id(db, "pol:A") == "pol:B"
        assert resolve_id(db, "pol:B") == "pol:B"
        assert resolve_id(db, "pol:does-not-exist") == "pol:does-not-exist"


def test_alias_cycle_raises(db_path: Path) -> None:
    a = PoliticianNode(id="pol:A", name="A")
    b = PoliticianNode(id="pol:B", name="B")
    with GraphDB.open(db_path) as db:
        upsert_node(db, a)
        upsert_node(db, b)
        set_alias(db, "pol:A", "pol:B", source="t")
        set_alias(db, "pol:B", "pol:A", source="t")
    with GraphDB.open(db_path) as db, pytest.raises(RuntimeError, match="cycle"):
        resolve_id(db, "pol:A")


def test_merge_rewrites_edges_and_external_ids(db_path: Path) -> None:
    """Full merge: external_ids move, edges get rewritten, alias is recorded."""
    fec_node = PoliticianNode(
        id="pol:H8CA17123",
        name="DOE, JANE",
        external_ids={"fec": "H8CA17123"},
        fec_candidate_id="H8CA17123",
    )
    bioguide_node = PoliticianNode(
        id="pol:D000123",
        name="Jane Doe",
        external_ids={"congress": "D000123"},
        bioguide_id="D000123",
    )
    pac = PACNode(id="pac:C00999", name="DONOR PAC")
    edge = DonationEdge(
        id="e:1",
        src_id="pac:C00999",
        dst_id="pol:H8CA17123",
        evidence_type="VERIFIED",
        source_name="fec",
        source_id="x",
        amount_cents=1000,
    )
    with GraphDB.open(db_path) as db:
        upsert_node(db, pac)
        upsert_node(db, fec_node)
        upsert_node(db, bioguide_node)
        upsert_edge(db, edge)
        merge_nodes(db, from_id="pol:H8CA17123", to_id="pol:D000123", source="test")

    with GraphDB.open(db_path) as db:
        # Source node is gone.
        assert db.conn.execute(
            "SELECT 1 FROM nodes WHERE id = ?", ("pol:H8CA17123",)
        ).fetchone() is None
        # Edge points at the canonical id now.
        kind, payload = get_node_payload(db, "pol:D000123") or (None, None)
        assert payload is not None
        edge_row = db.conn.execute(
            "SELECT dst_id FROM edges WHERE id = ?", ("e:1",)
        ).fetchone()
        assert edge_row["dst_id"] == "pol:D000123"
        # external_ids merged: both fec + congress live under canonical.
        assert find_node_by_external_id(db, "fec", "H8CA17123") == "pol:D000123"
        assert find_node_by_external_id(db, "congress", "D000123") == "pol:D000123"
        # Alias chain works.
        assert resolve_id(db, "pol:H8CA17123") == "pol:D000123"


# ---- write_member entity resolution --------------------------------------


def test_write_member_merges_existing_fec_node(db_path: Path, index) -> None:
    """The headline scenario: FEC ingested first, Congress ingest merges it."""
    fec_raw = FECCandidateRaw.model_validate(
        json.loads((FEC_FIXTURES / "candidates_page1.json").read_text())["results"][0]
    )
    member_payload = json.loads((FIXTURES / "member_detail_D000123.json").read_text())
    detail = MemberDetail.model_validate(member_payload["member"])

    with GraphDB.open(db_path) as db:
        write_candidate(db, fec_raw)  # creates pol:H8CA17123
    with GraphDB.open(db_path) as db:
        report = write_member(db, detail, index=index)

    assert report["id"] == "pol:D000123"
    assert "pol:H8CA17123" in report["merged_from"]

    with GraphDB.open(db_path) as db:
        # Only one politician now.
        n_pols = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = ?", ("Politician",)
        ).fetchone()[0]
        assert n_pols == 1
        # The canonical node holds both external ids.
        assert find_node_by_external_id(db, "fec", "H8CA17123") == "pol:D000123"
        assert find_node_by_external_id(db, "congress", "D000123") == "pol:D000123"
        # Backwards alias works.
        assert resolve_id(db, "pol:H8CA17123") == "pol:D000123"


def test_write_member_no_existing_fec_node_creates_clean(db_path: Path, index) -> None:
    """Congress ingested first: just writes the canonical, no merge."""
    member_payload = json.loads((FIXTURES / "member_detail_D000123.json").read_text())
    detail = MemberDetail.model_validate(member_payload["member"])
    with GraphDB.open(db_path) as db:
        report = write_member(db, detail, index=index)
    assert report["merged_from"] == []
    with GraphDB.open(db_path) as db:
        kind, payload = get_node_payload(db, "pol:D000123")
        node = node_from_row(kind, payload)
        assert isinstance(node, PoliticianNode)
        assert node.bioguide_id == "D000123"


def test_write_member_idempotent(db_path: Path, index) -> None:
    member_payload = json.loads((FIXTURES / "member_detail_D000123.json").read_text())
    detail = MemberDetail.model_validate(member_payload["member"])
    fec_raw = FECCandidateRaw.model_validate(
        json.loads((FEC_FIXTURES / "candidates_page1.json").read_text())["results"][0]
    )

    with GraphDB.open(db_path) as db:
        write_candidate(db, fec_raw)
        write_member(db, detail, index=index)
        # Re-running should produce zero new merges.
        report = write_member(db, detail, index=index)
        assert report["merged_from"] == []
        # And totals stay sane.
        n = db.conn.execute("SELECT COUNT(*) FROM nodes WHERE kind='Politician'").fetchone()[0]
        assert n == 1


# ---- committee write end-to-end ------------------------------------------


def test_write_committee_detail_creates_assignments(db_path: Path) -> None:
    payload = json.loads((FIXTURES / "committee_detail_hsju00.json").read_text())
    detail = CommitteeDetail.model_validate(payload["committee"])

    # Create the politician nodes the assignment edges point at.
    with GraphDB.open(db_path) as db:
        upsert_node(db, PoliticianNode(id="pol:D000123", name="Jane Doe"))
        upsert_node(db, PoliticianNode(id="pol:R000999", name="Richard Roe"))
    with GraphDB.open(db_path) as db:
        n = write_committee_detail(db, detail)

    assert n == 2
    with GraphDB.open(db_path) as db:
        stats = db.stats()
        assert stats["nodes_by_kind"].get("GovernmentBody") == 1
        assert stats["edges_by_kind"].get("CommitteeMembership") == 2
        # Inspect one edge.
        kind, payload = (
            db.conn.execute(
                "SELECT kind, payload FROM edges WHERE id = ?",
                ("congress:assign:hsju00:D000123",),
            ).fetchone()
        )
        edge = edge_from_row(kind, json.loads(payload))
        assert isinstance(edge, CommitteeMembershipEdge)
        assert edge.role == "chair"


def test_committee_assignment_edges_are_idempotent(db_path: Path) -> None:
    payload = json.loads((FIXTURES / "committee_detail_hsju00.json").read_text())
    detail = CommitteeDetail.model_validate(payload["committee"])
    with GraphDB.open(db_path) as db:
        upsert_node(db, PoliticianNode(id="pol:D000123", name="Jane Doe"))
        upsert_node(db, PoliticianNode(id="pol:R000999", name="Richard Roe"))
        write_committee_detail(db, detail)
        write_committee_detail(db, detail)
        write_committee_detail(db, detail)
    with GraphDB.open(db_path) as db:
        stats = db.stats()
        assert stats["edges_by_kind"]["CommitteeMembership"] == 2


# ---- resolve pass --------------------------------------------------------


def test_resolve_pass_handles_late_fec_ingest(db_path: Path, index) -> None:
    """Congress ingested first, then FEC: resolve_pass cleans up the duplicate."""
    member_payload = json.loads((FIXTURES / "member_detail_D000123.json").read_text())
    detail = MemberDetail.model_validate(member_payload["member"])
    fec_raw = FECCandidateRaw.model_validate(
        json.loads((FEC_FIXTURES / "candidates_page1.json").read_text())["results"][0]
    )

    with GraphDB.open(db_path) as db:
        write_member(db, detail, index=index)  # creates pol:D000123, no FEC node yet
    with GraphDB.open(db_path) as db:
        write_candidate(db, fec_raw)  # creates pol:H8CA17123 — orphan duplicate
    with GraphDB.open(db_path) as db:
        before = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='Politician'"
        ).fetchone()[0]
        assert before == 2

    from pge.sources.congress.ingest import resolve_pass
    with GraphDB.open(db_path) as db:
        report = resolve_pass(db, index=index)
    assert report["merges"] == 1

    with GraphDB.open(db_path) as db:
        after = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='Politician'"
        ).fetchone()[0]
        assert after == 1
        assert resolve_id(db, "pol:H8CA17123") == "pol:D000123"


# ---- schema bump regression ----------------------------------------------


def test_bootstrap_seeds_politicians_from_yaml(db_path: Path) -> None:
    """The build-time bake step populates Politicians with no API key."""
    from pge.sources.congress.bootstrap import bootstrap_politicians_from_yaml

    fixture = FIXTURES / "legislators.yaml"
    with GraphDB.open(db_path) as db:
        result = bootstrap_politicians_from_yaml(db, fixture)
    assert result["members"] == 3  # fixture has 3 legislators

    with GraphDB.open(db_path) as db:
        n = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='Politician'"
        ).fetchone()[0]
    assert n == 3

    with GraphDB.open(db_path) as db:
        kind, payload = get_node_payload(db, "pol:L000174")
        node = node_from_row(kind, payload)
    assert isinstance(node, PoliticianNode)
    assert node.bioguide_id == "L000174"
    assert node.chamber == "senate"
    assert node.party == "DEM"
    assert node.state == "VT"
    # No FEC ext id — that lands later via the merge path.
    assert node.external_ids == {"congress": "L000174"}


def test_bootstrap_idempotent(db_path: Path) -> None:
    from pge.sources.congress.bootstrap import bootstrap_politicians_from_yaml

    fixture = FIXTURES / "legislators.yaml"
    with GraphDB.open(db_path) as db:
        bootstrap_politicians_from_yaml(db, fixture)
        bootstrap_politicians_from_yaml(db, fixture)
    with GraphDB.open(db_path) as db:
        n = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind='Politician'"
        ).fetchone()[0]
    assert n == 3


def test_bootstrap_then_fec_ingest_resolves_cleanly(db_path: Path, index) -> None:
    """The order of operations the deploy uses: bootstrap (no key) first,
    then a later FEC ingest should still merge into the canonical bioguide
    node without orphaning anything."""
    from pge.sources.congress.bootstrap import bootstrap_politicians_from_yaml
    from pge.sources.congress.ingest import resolve_pass

    fixture = FIXTURES / "legislators.yaml"
    fec_raw = FECCandidateRaw.model_validate(
        json.loads((FEC_FIXTURES / "candidates_page1.json").read_text())["results"][0]
    )

    with GraphDB.open(db_path) as db:
        bootstrap_politicians_from_yaml(db, fixture)  # creates pol:D000123 etc.
    with GraphDB.open(db_path) as db:
        write_candidate(db, fec_raw)  # creates pol:H8CA17123
    with GraphDB.open(db_path) as db:
        resolve_pass(db, index=index)

    with GraphDB.open(db_path) as db:
        # The FEC-keyed node has been merged into the bioguide-keyed one.
        from pge.graph.aliases import find_node_by_external_id
        assert find_node_by_external_id(db, "fec", "H8CA17123") == "pol:D000123"
        assert find_node_by_external_id(db, "congress", "D000123") == "pol:D000123"


def test_schema_includes_aliases_table(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        tables = {
            row["name"]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "aliases" in tables
