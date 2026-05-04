"""FastAPI endpoint tests using starlette's TestClient.

The app is constructed via :func:`create_app` so each test gets its own DB.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pge.api.app import create_app
from pge.graph.aliases import set_alias
from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import upsert_edge, upsert_node
from pge.schema.edges import CommitteeMembershipEdge, DonationEdge
from pge.schema.nodes import GovernmentBodyNode, PACNode, PoliticianNode


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    path = tmp_path / "pge.db"
    init_db(path)
    with GraphDB.open(path) as db:
        upsert_node(db, PoliticianNode(id="pol:A", name="Alice", state="CA"))
        upsert_node(db, PoliticianNode(id="pol:B", name="Bob", state="NY"))
        upsert_node(db, PACNode(id="pac:1", name="Industry PAC", pac_type="trade"))
        upsert_node(db, GovernmentBodyNode(id="gov:hsju00", name="Judiciary", body_type="committee"))
        upsert_edge(db, DonationEdge(
            id="e:donation", src_id="pac:1", dst_id="pol:A",
            evidence_type="VERIFIED", source_name="fec", source_id="t1",
            amount_cents=50000, as_of_date=date(2024, 5, 1),
            strength="strong", confidence="high",
        ))
        upsert_edge(db, CommitteeMembershipEdge(
            id="e:assign-A", src_id="pol:A", dst_id="gov:hsju00",
            evidence_type="VERIFIED", source_name="congress",
            source_id="hsju00/A", role="chair",
        ))
        upsert_edge(db, CommitteeMembershipEdge(
            id="e:assign-B", src_id="pol:B", dst_id="gov:hsju00",
            evidence_type="VERIFIED", source_name="congress",
            source_id="hsju00/B", role="member",
        ))
        set_alias(db, "pol:fec123", "pol:A", source="test")
    return path


@pytest.fixture
def client(populated_db: Path) -> TestClient:
    return TestClient(create_app(populated_db))


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["nodes_total"] == 4
    assert body["edges_total"] == 3


def test_health_503_when_db_missing(tmp_path: Path) -> None:
    app = create_app(tmp_path / "nope.db")
    c = TestClient(app)
    resp = c.get("/health")
    assert resp.status_code == 503
    assert "no database" in resp.json()["detail"]


def test_get_node_ok(client: TestClient) -> None:
    resp = client.get("/nodes/pol:A")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "pol:A"
    assert body["kind"] == "Politician"
    assert body["name"] == "Alice"


def test_get_node_404(client: TestClient) -> None:
    resp = client.get("/nodes/pol:nope")
    assert resp.status_code == 404


def test_search_nodes_no_filters_returns_all(client: TestClient) -> None:
    resp = client.get("/nodes")
    assert resp.status_code == 200
    body = resp.json()
    assert {n["id"] for n in body["nodes"]} == {
        "pol:A", "pol:B", "pac:1", "gov:hsju00",
    }
    assert body["limit"] == 20
    assert body["offset"] == 0


def test_search_nodes_filter_by_kind(client: TestClient) -> None:
    resp = client.get("/nodes?kind=Politician")
    assert resp.status_code == 200
    ids = {n["id"] for n in resp.json()["nodes"]}
    assert ids == {"pol:A", "pol:B"}


def test_search_nodes_substring_query_case_insensitive(client: TestClient) -> None:
    resp = client.get("/nodes?q=alice")
    assert resp.status_code == 200
    ids = {n["id"] for n in resp.json()["nodes"]}
    assert ids == {"pol:A"}


def test_search_nodes_pagination(client: TestClient) -> None:
    resp = client.get("/nodes?limit=2&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["nodes"]) == 2
    # Second page picks up the remaining ids.
    resp2 = client.get("/nodes?limit=2&offset=2")
    body2 = resp2.json()
    assert len(body2["nodes"]) == 2
    # No id appears in both pages.
    assert {n["id"] for n in body["nodes"]}.isdisjoint(
        {n["id"] for n in body2["nodes"]}
    )


def test_search_nodes_invalid_limit_rejected(client: TestClient) -> None:
    resp = client.get("/nodes?limit=0")
    assert resp.status_code == 422
    resp = client.get("/nodes?limit=999")
    assert resp.status_code == 422


def test_map_politicians_returns_geocoded_only(populated_db: Path) -> None:
    """Adds a geocoded politician + ensures the /map endpoint returns it
    while skipping anything without lat/lng."""
    from pge.graph.ingest import upsert_node as node_upsert
    with GraphDB.open(populated_db) as db:
        node_upsert(
            db,
            PoliticianNode(
                id="pol:geo1", name="Geo Senator", state="CA",
                chamber="senate", party="DEM",
                latitude=38.5816, longitude=-121.4944,
            ),
        )
    c = TestClient(create_app(populated_db))
    resp = c.get("/map/politicians")
    assert resp.status_code == 200
    body = resp.json()
    ids = {p["id"] for p in body["politicians"]}
    assert "pol:geo1" in ids
    # The fixture's pol:A and pol:B don't have lat/lng -> skipped.
    assert "pol:A" not in ids


def test_map_politicians_filter_by_party(populated_db: Path) -> None:
    from pge.graph.ingest import upsert_node as node_upsert
    with GraphDB.open(populated_db) as db:
        node_upsert(db, PoliticianNode(
            id="pol:dem", name="Dem", state="CA", chamber="senate",
            party="DEM", latitude=38.0, longitude=-121.0,
        ))
        node_upsert(db, PoliticianNode(
            id="pol:rep", name="Rep", state="TX", chamber="senate",
            party="REP", latitude=30.0, longitude=-97.0,
        ))
    c = TestClient(create_app(populated_db))
    resp = c.get("/map/politicians?party=DEM")
    ids = {p["id"] for p in resp.json()["politicians"]}
    assert "pol:dem" in ids
    assert "pol:rep" not in ids


def test_map_connections_aggregates_by_company(populated_db: Path) -> None:
    """Build a small Company -> PAC -> Politician chain and verify the
    /map/connections endpoint walks it correctly."""
    from pge.graph.ingest import upsert_edge as edge_upsert
    from pge.graph.ingest import upsert_node as node_upsert
    from pge.schema.edges import BusinessPartnershipEdge, DonationEdge
    from pge.schema.nodes import CompanyNode, PACNode

    with GraphDB.open(populated_db) as db:
        node_upsert(db, PoliticianNode(
            id="pol:focus", name="Focus", state="CA",
            chamber="senate", party="DEM",
            latitude=38.5, longitude=-121.5,
        ))
        node_upsert(db, CompanyNode(
            id="co:seed:test", name="Test Inc",
            domain="test.com", hq_city="X", hq_state="NY",
            latitude=40.0, longitude=-74.0,
        ))
        node_upsert(db, PACNode(
            id="pac:test1", name="Test PAC 1", pac_type="corporate",
            fec_committee_id="C1",
        ))
        node_upsert(db, PACNode(
            id="pac:test2", name="Test PAC 2", pac_type="corporate",
            fec_committee_id="C2",
        ))
        edge_upsert(db, BusinessPartnershipEdge(
            id="bp:1", src_id="co:seed:test", dst_id="pac:test1",
            evidence_type="VERIFIED", source_name="t", source_id="x",
            relation="parent",
        ))
        edge_upsert(db, BusinessPartnershipEdge(
            id="bp:2", src_id="co:seed:test", dst_id="pac:test2",
            evidence_type="VERIFIED", source_name="t", source_id="y",
            relation="parent",
        ))
        edge_upsert(db, DonationEdge(
            id="d:1", src_id="pac:test1", dst_id="pol:focus",
            evidence_type="VERIFIED", source_name="t", source_id="d1",
            amount_cents=50000,  # $500
        ))
        edge_upsert(db, DonationEdge(
            id="d:2", src_id="pac:test2", dst_id="pol:focus",
            evidence_type="VERIFIED", source_name="t", source_id="d2",
            amount_cents=75000,  # $750
        ))

    c = TestClient(create_app(populated_db))
    resp = c.get("/map/connections/pol:focus")
    assert resp.status_code == 200
    body = resp.json()
    assert body["politician"]["id"] == "pol:focus"
    assert len(body["connections"]) == 1
    conn = body["connections"][0]
    assert conn["company_id"] == "co:seed:test"
    assert conn["total_cents"] == 125000  # $500 + $750
    assert conn["domain"] == "test.com"
    assert conn["logo_url"] == "https://logo.clearbit.com/test.com"
    assert {p["id"] for p in conn["pacs"]} == {"pac:test1", "pac:test2"}


def test_map_connections_404_for_missing_politician(client: TestClient) -> None:
    resp = client.get("/map/connections/pol:nope")
    assert resp.status_code == 404


def test_cors_headers_present(client: TestClient) -> None:
    """CORS preflight should succeed for cross-origin browser requests."""
    resp = client.get(
        "/health",
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_get_node_resolves_alias(client: TestClient) -> None:
    resp = client.get("/nodes/pol:fec123")
    assert resp.status_code == 200
    assert resp.json()["id"] == "pol:A"


def test_neighbors_default_depth(client: TestClient) -> None:
    resp = client.get("/nodes/pol:A/neighbors")
    assert resp.status_code == 200
    body = resp.json()
    node_ids = {n["id"] for n in body["nodes"]}
    assert node_ids == {"pol:A", "pac:1", "gov:hsju00"}
    edge_ids = {e["id"] for e in body["edges"]}
    assert edge_ids == {"e:donation", "e:assign-A"}
    # Edges expose evidence/confidence at the top level (dossier shape).
    donation = next(e for e in body["edges"] if e["id"] == "e:donation")
    assert donation["evidence_type"] == "VERIFIED"
    assert donation["confidence"] == "high"
    assert donation["strength"] == "strong"


def test_neighbors_with_filters(client: TestClient) -> None:
    resp = client.get("/nodes/pol:A/neighbors?edge_kind=Donation")
    assert resp.status_code == 200
    edge_ids = {e["id"] for e in resp.json()["edges"]}
    assert edge_ids == {"e:donation"}


def test_neighbors_invalid_depth_rejected(client: TestClient) -> None:
    resp = client.get("/nodes/pol:A/neighbors?depth=0")
    assert resp.status_code == 422  # Pydantic validation


def test_neighbors_404_for_missing_node(client: TestClient) -> None:
    resp = client.get("/nodes/pol:nope/neighbors")
    assert resp.status_code == 404


def test_paths_direct(client: TestClient) -> None:
    resp = client.get("/paths", params={"from": "pac:1", "to": "pol:A"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["paths"]) == 1
    assert body["paths"][0][0]["edge_id"] == "e:donation"


def test_paths_multi_hop(client: TestClient) -> None:
    resp = client.get(
        "/paths",
        params={"from": "pac:1", "to": "pol:B", "max_depth": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["paths"]) >= 1
    # The dossier-style returned subgraph references all path nodes.
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"pac:1", "pol:A", "gov:hsju00", "pol:B"} <= node_ids


def test_paths_max_depth_validation(client: TestClient) -> None:
    resp = client.get(
        "/paths",
        params={"from": "pac:1", "to": "pol:B", "max_depth": 99},
    )
    assert resp.status_code == 422


def test_paths_404_for_missing_endpoint(client: TestClient) -> None:
    resp = client.get(
        "/paths",
        params={"from": "pol:nope", "to": "pol:A"},
    )
    assert resp.status_code == 404


def test_edges_between_endpoint(client: TestClient) -> None:
    resp = client.get("/edges-between", params={"a": "pac:1", "b": "pol:A"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["edges"]) == 1
    assert body["edges"][0]["id"] == "e:donation"


def test_openapi_doc_renders(client: TestClient) -> None:
    """Sanity: FastAPI's OpenAPI generation isn't broken by our type usage."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/nodes/{node_id}" in schema["paths"]
    assert "/paths" in schema["paths"]
