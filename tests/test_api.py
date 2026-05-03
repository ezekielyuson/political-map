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
