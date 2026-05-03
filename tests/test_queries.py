"""Query layer tests: get_node, neighbors, edges_between, find_paths."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pge.graph.aliases import set_alias
from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import upsert_edge, upsert_node
from pge.graph.queries import (
    edges_between,
    find_paths,
    get_node,
    neighbors,
)
from pge.schema.edges import (
    CommitteeMembershipEdge,
    DonationEdge,
    LobbyingContractEdge,
)
from pge.schema.nodes import (
    CompanyNode,
    GovernmentBodyNode,
    LobbyingFirmNode,
    PACNode,
    PoliticianNode,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A populated DB modelling: PAC -> Politician -> Committee, plus a
    Company -> LobbyingFirm contract. Two politicians with one shared committee
    so we have a 2-hop path between PAC and Politician #2 via Politician #1."""
    path = tmp_path / "pge.db"
    init_db(path)

    nodes = [
        PoliticianNode(id="pol:A", name="Alice", state="CA", chamber="house"),
        PoliticianNode(id="pol:B", name="Bob", state="NY", chamber="house"),
        PACNode(id="pac:1", name="Industry PAC", pac_type="trade"),
        GovernmentBodyNode(id="gov:hsju00", name="Judiciary", body_type="committee"),
        CompanyNode(id="co:42", name="Megacorp"),
        LobbyingFirmNode(id="lf:7", name="K Street"),
    ]
    edges = [
        DonationEdge(
            id="e:donation",
            src_id="pac:1",
            dst_id="pol:A",
            evidence_type="VERIFIED",
            source_name="fec",
            source_id="t1",
            amount_cents=50000,
            as_of_date=date(2024, 5, 1),
            strength="strong",
            confidence="high",
        ),
        CommitteeMembershipEdge(
            id="e:assign-A",
            src_id="pol:A",
            dst_id="gov:hsju00",
            evidence_type="VERIFIED",
            source_name="congress",
            source_id="hsju00/A",
            role="chair",
        ),
        CommitteeMembershipEdge(
            id="e:assign-B",
            src_id="pol:B",
            dst_id="gov:hsju00",
            evidence_type="VERIFIED",
            source_name="congress",
            source_id="hsju00/B",
            role="member",
        ),
        LobbyingContractEdge(
            id="e:lobby",
            src_id="co:42",
            dst_id="lf:7",
            evidence_type="VERIFIED",
            source_name="lda",
            source_id="f1",
            amount_cents=1_000_000,
            issue_codes=["TAX"],
        ),
    ]
    with GraphDB.open(path) as db:
        for n in nodes:
            upsert_node(db, n)
        for e in edges:
            upsert_edge(db, e)
    return path


# ---- get_node ------------------------------------------------------------


def test_get_node_returns_basic_shape(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        view = get_node(db, "pol:A")
    assert view is not None
    assert view.id == "pol:A"
    assert view.kind == "Politician"
    assert view.name == "Alice"
    assert view.attrs["state"] == "CA"
    assert view.attrs["chamber"] == "house"


def test_get_node_returns_none_for_missing(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        assert get_node(db, "pol:does-not-exist") is None


def test_get_node_resolves_aliases(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        # Pretend pol:fec123 was merged into pol:A.
        set_alias(db, "pol:fec123", "pol:A", source="test")
    with GraphDB.open(db_path) as db:
        view = get_node(db, "pol:fec123")
    assert view is not None
    assert view.id == "pol:A"


# ---- neighbors -----------------------------------------------------------


def test_neighbors_depth_1(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        sg = neighbors(db, "pol:A", depth=1)
    node_ids = {n.id for n in sg.nodes}
    assert node_ids == {"pol:A", "pac:1", "gov:hsju00"}
    edge_ids = {e.id for e in sg.edges}
    assert edge_ids == {"e:donation", "e:assign-A"}


def test_neighbors_depth_2_reaches_other_politician(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        sg = neighbors(db, "pac:1", depth=2)
    # PAC -> donation -> Alice -> assign -> Judiciary -> assign -> Bob
    # Depth 2 hops from pac:1: pac:1 -> pol:A (1 hop), pol:A -> gov:hsju00 (2 hops).
    # We DO surface gov:hsju00 (and the assign-A edge) at depth 2, but not pol:B.
    node_ids = {n.id for n in sg.nodes}
    assert node_ids == {"pac:1", "pol:A", "gov:hsju00"}
    edge_ids = {e.id for e in sg.edges}
    assert edge_ids == {"e:donation", "e:assign-A"}


def test_neighbors_depth_3_reaches_other_politician(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        sg = neighbors(db, "pac:1", depth=3)
    node_ids = {n.id for n in sg.nodes}
    assert "pol:B" in node_ids
    assert "e:assign-B" in {e.id for e in sg.edges}


def test_neighbors_filters_by_edge_kind(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        sg = neighbors(db, "pol:A", depth=1, edge_kinds=["Donation"])
    edge_ids = {e.id for e in sg.edges}
    assert edge_ids == {"e:donation"}
    node_ids = {n.id for n in sg.nodes}
    # Only the donation edge surfaces, so neighbors are pol:A + pac:1.
    assert node_ids == {"pol:A", "pac:1"}


def test_neighbors_filters_by_node_kind_post_traversal(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        sg = neighbors(db, "pol:A", depth=1, node_kinds=["Politician"])
    # Edges are the same; only the Politician node survives the filter.
    assert {n.id for n in sg.nodes} == {"pol:A"}
    assert {e.id for e in sg.edges} == {"e:donation", "e:assign-A"}


def test_neighbors_filters_by_evidence_type(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        # Add a REPORTED edge to test the filter.
        from pge.schema.edges import VotingAlignmentEdge
        upsert_edge(
            db,
            VotingAlignmentEdge(
                id="e:align",
                src_id="pol:A",
                dst_id="pol:B",
                evidence_type="INFERRED",
                source_name="internal",
                source_id="x",
                alignment_score=0.85,
                sample_size=120,
            ),
        )
    with GraphDB.open(db_path) as db:
        sg = neighbors(db, "pol:A", depth=1, evidence_types=["VERIFIED"])
    edge_ids = {e.id for e in sg.edges}
    assert "e:align" not in edge_ids
    assert {"e:donation", "e:assign-A"} <= edge_ids


def test_neighbors_invalid_depth(db_path: Path) -> None:
    with GraphDB.open(db_path) as db, pytest.raises(ValueError):
        neighbors(db, "pol:A", depth=0)


def test_neighbors_resolves_aliases(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        set_alias(db, "pol:alias", "pol:A", source="test")
    with GraphDB.open(db_path) as db:
        sg = neighbors(db, "pol:alias", depth=1)
    assert {n.id for n in sg.nodes} == {"pol:A", "pac:1", "gov:hsju00"}


# ---- edges_between -------------------------------------------------------


def test_edges_between_undirected(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        e1 = edges_between(db, "pac:1", "pol:A")
        e2 = edges_between(db, "pol:A", "pac:1")
    assert {e.id for e in e1} == {"e:donation"}
    assert {e.id for e in e2} == {"e:donation"}  # undirected by default


def test_edges_between_directed_drops_reverse(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        forward = edges_between(db, "pac:1", "pol:A", directed=True)
        backward = edges_between(db, "pol:A", "pac:1", directed=True)
    assert {e.id for e in forward} == {"e:donation"}
    assert backward == []


def test_edges_between_resolves_aliases(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        set_alias(db, "pol:alias", "pol:A", source="test")
    with GraphDB.open(db_path) as db:
        edges = edges_between(db, "pac:1", "pol:alias")
    assert {e.id for e in edges} == {"e:donation"}


# ---- find_paths ----------------------------------------------------------


def test_find_paths_direct(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pac:1", "pol:A", max_depth=1)
    assert len(result.paths) == 1
    path = result.paths[0]
    assert len(path) == 1
    assert path[0].edge_id == "e:donation"
    assert path[0].from_node == "pac:1"
    assert path[0].to_node == "pol:A"


def test_find_paths_two_hops(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pac:1", "gov:hsju00", max_depth=3)
    # PAC -> donation -> pol:A -> assign -> committee
    assert any(len(p) == 2 for p in result.paths)
    # All paths should end at gov:hsju00.
    for path in result.paths:
        assert path[-1].to_node == "gov:hsju00"


def test_find_paths_three_hops_pac_to_other_politician(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pac:1", "pol:B", max_depth=3)
    # PAC -> Alice -> Judiciary -> Bob is 3 hops.
    assert len(result.paths) >= 1
    p = result.paths[0]
    assert len(p) == 3
    assert p[0].from_node == "pac:1"
    assert p[-1].to_node == "pol:B"


def test_find_paths_max_depth_cuts_off(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pac:1", "pol:B", max_depth=2)
    # Distance is 3; cap at 2 means no path returns.
    assert result.paths == []


def test_find_paths_self_returns_empty(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pol:A", "pol:A")
    assert result.paths == []


def test_find_paths_disconnected(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pac:1", "co:42", max_depth=4)
    assert result.paths == []


def test_find_paths_collects_referenced_nodes_and_edges(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pac:1", "pol:B", max_depth=3)
    node_ids = {n.id for n in result.nodes}
    assert {"pac:1", "pol:A", "gov:hsju00", "pol:B"} <= node_ids
    edge_ids = {e.id for e in result.edges}
    assert edge_ids == {"e:donation", "e:assign-A", "e:assign-B"}


def test_find_paths_resolves_aliases(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        set_alias(db, "pol:alias", "pol:A", source="test")
    with GraphDB.open(db_path) as db:
        result = find_paths(db, "pac:1", "pol:alias", max_depth=1)
    assert len(result.paths) == 1
    assert result.paths[0][0].to_node == "pol:A"


def test_find_paths_invalid_args(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        with pytest.raises(ValueError):
            find_paths(db, "pol:A", "pol:B", max_depth=0)
        with pytest.raises(ValueError):
            find_paths(db, "pol:A", "pol:B", max_paths=0)
