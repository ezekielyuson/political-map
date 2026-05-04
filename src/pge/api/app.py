"""FastAPI app exposing read-only graph queries.

No auth -- per spec, this is local-dev only. ``create_app(db_path=...)`` lets
tests construct an isolated app pointed at a tmp DB.

Endpoints:

* ``GET /health``                       -- liveness ping with DB row counts.
* ``GET /nodes/{id}``                   -- single node, alias-resolved.
* ``GET /nodes/{id}/neighbors``         -- bounded BFS subgraph.
* ``GET /paths?from_=&to_=&max_depth=&max_paths=`` -- paths between two nodes.

Why ``from_`` / ``to_``? ``from`` is a Python keyword. FastAPI's ``Query(alias=...)``
maps the URL parameter ``from`` to the python ``from_`` argument; same for ``to``.

We deliberately do **not** use ``from __future__ import annotations`` here.
FastAPI inspects function signatures at decoration time to build its dependency
graph; with PEP 563 annotations FastAPI sees ``Annotated[GraphDB, Depends(...)]``
as a string and fails to resolve ``Depends``, which silently turns it into
a query parameter and 422s every request.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from pge.graph.db import DEFAULT_DB_PATH, GraphDB
from pge.graph.queries import (
    NodeView,
    PathsView,
    Subgraph,
    edges_between,
    find_paths,
    get_node,
    list_nodes,
    neighbors,
)


def _resolve_db_path() -> Path:
    """Honor ``PGE_DB_PATH`` env var, falling back to ``DEFAULT_DB_PATH``."""
    return Path(os.environ.get("PGE_DB_PATH", str(DEFAULT_DB_PATH)))


def create_app(db_path: Path | None = None) -> FastAPI:
    """Construct the FastAPI app with an explicit DB path.

    The default factory uses ``PGE_DB_PATH`` (or ``data/pge.db``); tests can
    bypass it by passing ``db_path=tmp_path/...``.
    """
    db_path = Path(db_path) if db_path is not None else _resolve_db_path()

    application = FastAPI(
        title="Political Graph Engine",
        version="0.1.0",
        description="Local-dev API over the PGE relationship graph.",
    )

    # The API is read-only and serves public political data, so wide-open
    # CORS is fine. If you ever add write endpoints, lock this down to your
    # frontend's origin via ``allow_origins=[...]`` instead.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def get_db() -> Iterator[GraphDB]:
        if not db_path.exists():
            raise HTTPException(
                status_code=503,
                detail=f"no database at {db_path}; run `pge db init` first",
            )
        with GraphDB.open(db_path) as db:
            yield db

    @application.get("/")
    def index() -> dict:
        """Directory of available endpoints. Cheap, no DB hit -- so it works
        even when ``/data`` is empty or the volume hasn't mounted yet."""
        return {
            "service": "Political Graph Engine",
            "version": "0.1.0",
            "endpoints": {
                "health": "/health",
                "docs": "/docs",
                "openapi": "/openapi.json",
                "node": "/nodes/{id}",
                "neighbors": "/nodes/{id}/neighbors?depth=1",
                "paths": "/paths?from=<id>&to=<id>&max_depth=3",
                "edges_between": "/edges-between?a=<id>&b=<id>",
            },
        }

    @application.get("/health")
    def health(db: Annotated[GraphDB, Depends(get_db)]) -> dict:
        return {"ok": True, **db.stats()}

    @application.get("/nodes")
    def search_nodes(
        db: Annotated[GraphDB, Depends(get_db)],
        kind: Annotated[str | None, Query(description="Node kind filter (e.g. 'Politician').")] = None,
        q: Annotated[str | None, Query(description="Case-insensitive substring match on name.")] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict:
        """Browse / search nodes by kind and/or name."""
        return {
            "nodes": list_nodes(db, kind=kind, q=q, limit=limit, offset=offset),
            "limit": limit,
            "offset": offset,
        }

    @application.get("/nodes/{node_id}", response_model=NodeView)
    def read_node(
        node_id: str, db: Annotated[GraphDB, Depends(get_db)]
    ) -> NodeView:
        node = get_node(db, node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"unknown node: {node_id}")
        return node

    @application.get("/nodes/{node_id}/neighbors", response_model=Subgraph)
    def read_neighbors(
        node_id: str,
        db: Annotated[GraphDB, Depends(get_db)],
        depth: Annotated[int, Query(ge=1, le=4)] = 1,
        edge_kind: Annotated[list[str] | None, Query()] = None,
        node_kind: Annotated[list[str] | None, Query()] = None,
        evidence_type: Annotated[list[str] | None, Query()] = None,
        edge_limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
    ) -> Subgraph:
        # Verify the source node exists -- avoids returning an empty subgraph
        # silently when the id is misspelled.
        if get_node(db, node_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown node: {node_id}")
        return neighbors(
            db,
            node_id,
            depth=depth,
            edge_kinds=edge_kind,
            node_kinds=node_kind,
            evidence_types=evidence_type,
            edge_limit=edge_limit,
        )

    @application.get("/paths", response_model=PathsView)
    def read_paths(
        db: Annotated[GraphDB, Depends(get_db)],
        from_: Annotated[str, Query(alias="from")],
        to: Annotated[str, Query()],
        max_depth: Annotated[int, Query(ge=1, le=5)] = 3,
        max_paths: Annotated[int, Query(ge=1, le=50)] = 10,
        edge_kind: Annotated[list[str] | None, Query()] = None,
        evidence_type: Annotated[list[str] | None, Query()] = None,
    ) -> PathsView:
        if get_node(db, from_) is None:
            raise HTTPException(status_code=404, detail=f"unknown node: {from_}")
        if get_node(db, to) is None:
            raise HTTPException(status_code=404, detail=f"unknown node: {to}")
        return find_paths(
            db,
            from_,
            to,
            max_depth=max_depth,
            max_paths=max_paths,
            edge_kinds=edge_kind,
            evidence_types=evidence_type,
        )

    @application.get("/edges-between")
    def read_edges_between(
        db: Annotated[GraphDB, Depends(get_db)],
        a: Annotated[str, Query()],
        b: Annotated[str, Query()],
        evidence_type: Annotated[list[str] | None, Query()] = None,
        directed: Annotated[bool, Query()] = False,
    ) -> dict:
        return {
            "edges": edges_between(
                db, a, b, evidence_types=evidence_type, directed=directed
            )
        }

    return application


# Module-level app for ``uvicorn pge.api:app``.
app = create_app()
