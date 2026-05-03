"""Read-only graph traversals.

Three primitives:

* :func:`get_node`        -- single node lookup, alias-resolved.
* :func:`neighbors`       -- bounded BFS, returns the induced subgraph.
* :func:`edges_between`   -- direct edges between two nodes (any direction).
* :func:`find_paths`      -- shortest paths up to ``max_depth``, alias-resolved.

Result shapes
-------------
Every function returns Pydantic models (:class:`NodeView`, :class:`EdgeView`,
:class:`Subgraph`, :class:`PathsView`) so the API layer can serve them as JSON
without further conversion. The structured fields on each model match the
"dossier" shape called out in the spec: edges carry ``evidence_type``,
``strength``, ``confidence``, and provenance fields up front.

Why pull the full payload back as ``attrs``?
--------------------------------------------
Most consumers only need the typed fields (id/kind/name/evidence/etc.). The
remainder of the model -- e.g. a Donation's ``amount_cents``, a committee
membership's ``role`` -- lives under ``attrs``. Consumers that want full
typing can re-validate via :func:`pge.schema.nodes.node_from_row` /
:func:`pge.schema.edges.edge_from_row`.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pge.graph.aliases import resolve_id
from pge.graph.db import GraphDB

# Hot-path edge fields lifted into top-level columns; everything else lives in
# the JSON payload. We project the same shape from both sources here so the
# rest of the module reads uniformly.
_EDGE_COLS = (
    "id, kind, src_id, dst_id, evidence_type, source_name, source_id, "
    "as_of_date, payload"
)
_NODE_COLS = "id, kind, name, payload"


# ---- response shapes ------------------------------------------------------


class NodeView(BaseModel):
    """A node as the API / consumers see it."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    name: str
    attrs: dict[str, Any] = Field(default_factory=dict)


class EdgeView(BaseModel):
    """An edge with provenance and epistemic fields foregrounded."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    src_id: str
    dst_id: str
    evidence_type: str
    source_name: str
    source_id: str
    as_of_date: str | None = None
    strength: str | None = None
    confidence: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class Subgraph(BaseModel):
    """A bag of nodes + edges. ``edges`` may reference nodes not in ``nodes``
    when traversal hits the depth boundary."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[NodeView] = Field(default_factory=list)
    edges: list[EdgeView] = Field(default_factory=list)


class PathHop(BaseModel):
    """One step on a path: ``edge_id`` connects ``from_node`` to ``to_node``."""

    model_config = ConfigDict(extra="forbid")

    edge_id: str
    from_node: str
    to_node: str


class PathsView(BaseModel):
    """All discovered paths, plus the deduped node/edge sets they reference."""

    model_config = ConfigDict(extra="forbid")

    paths: list[list[PathHop]] = Field(default_factory=list)
    nodes: list[NodeView] = Field(default_factory=list)
    edges: list[EdgeView] = Field(default_factory=list)


# ---- row -> view --------------------------------------------------------


def _node_view_from_row(row) -> NodeView:
    payload = json.loads(row["payload"])
    # Hot fields are also in payload; expose them via the typed columns and
    # drop them from attrs so we don't double-serialize.
    attrs = {k: v for k, v in payload.items() if k not in {"id", "kind", "name"}}
    return NodeView(id=row["id"], kind=row["kind"], name=row["name"], attrs=attrs)


def _edge_view_from_row(row) -> EdgeView:
    payload = json.loads(row["payload"])
    keep = {"id", "kind", "src_id", "dst_id", "evidence_type", "source_id",
            "source_name", "as_of_date", "strength", "confidence"}
    attrs = {k: v for k, v in payload.items() if k not in keep}
    return EdgeView(
        id=row["id"],
        kind=row["kind"],
        src_id=row["src_id"],
        dst_id=row["dst_id"],
        evidence_type=row["evidence_type"],
        source_name=row["source_name"],
        source_id=row["source_id"],
        as_of_date=row["as_of_date"],
        strength=payload.get("strength"),
        confidence=payload.get("confidence"),
        attrs=attrs,
    )


# ---- query helpers -------------------------------------------------------


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


def _fetch_nodes(db: GraphDB, ids: Iterable[str]) -> dict[str, NodeView]:
    ids = [i for i in dict.fromkeys(ids)]  # dedupe, preserve order
    if not ids:
        return {}
    rows = db.conn.execute(
        f"SELECT {_NODE_COLS} FROM nodes WHERE id IN ({_placeholders(len(ids))})",
        ids,
    ).fetchall()
    return {r["id"]: _node_view_from_row(r) for r in rows}


# ---- public API ----------------------------------------------------------


def get_node(db: GraphDB, node_id: str) -> NodeView | None:
    """Resolve aliases and return the node (or None if missing)."""
    canonical = resolve_id(db, node_id)
    row = db.conn.execute(
        f"SELECT {_NODE_COLS} FROM nodes WHERE id = ?", (canonical,)
    ).fetchone()
    if row is None:
        return None
    return _node_view_from_row(row)


def neighbors(
    db: GraphDB,
    node_id: str,
    *,
    depth: int = 1,
    edge_kinds: Sequence[str] | None = None,
    node_kinds: Sequence[str] | None = None,
    evidence_types: Sequence[str] | None = None,
    edge_limit: int = 1000,
) -> Subgraph:
    """Return the depth-bounded subgraph around ``node_id``.

    * ``depth``           hops to walk (default 1).
    * ``edge_kinds``      restrict to these edge kinds.
    * ``node_kinds``      filter the *returned* node set (edges to filtered-out
                          nodes are still discovered, then dropped).
    * ``evidence_types``  restrict to these evidence types.
    * ``edge_limit``      hard cap on edges returned, defends against stars.
    """
    if depth < 1:
        raise ValueError("depth must be >= 1")

    canonical = resolve_id(db, node_id)
    visited_node_ids: set[str] = {canonical}
    edges_by_id: dict[str, EdgeView] = {}
    frontier: set[str] = {canonical}

    for _ in range(depth):
        if not frontier or len(edges_by_id) >= edge_limit:
            break

        params: list[Any] = []
        ids = list(frontier)
        # src_id IN (frontier) OR dst_id IN (frontier)
        where = (
            f"(src_id IN ({_placeholders(len(ids))}) "
            f"OR dst_id IN ({_placeholders(len(ids))}))"
        )
        params.extend(ids)
        params.extend(ids)

        if edge_kinds:
            where += f" AND kind IN ({_placeholders(len(edge_kinds))})"
            params.extend(edge_kinds)
        if evidence_types:
            where += f" AND evidence_type IN ({_placeholders(len(evidence_types))})"
            params.extend(evidence_types)

        rows = db.conn.execute(
            f"SELECT {_EDGE_COLS} FROM edges WHERE {where} LIMIT ?",
            (*params, edge_limit - len(edges_by_id)),
        ).fetchall()

        new_frontier: set[str] = set()
        for row in rows:
            if row["id"] in edges_by_id:
                continue
            edges_by_id[row["id"]] = _edge_view_from_row(row)
            for nid in (row["src_id"], row["dst_id"]):
                if nid not in visited_node_ids:
                    new_frontier.add(nid)

        visited_node_ids |= new_frontier
        frontier = new_frontier

    nodes_by_id = _fetch_nodes(db, visited_node_ids)
    if node_kinds:
        kinds_set = set(node_kinds)
        nodes_by_id = {nid: n for nid, n in nodes_by_id.items() if n.kind in kinds_set}

    return Subgraph(
        nodes=list(nodes_by_id.values()),
        edges=list(edges_by_id.values()),
    )


def edges_between(
    db: GraphDB,
    a: str,
    b: str,
    *,
    evidence_types: Sequence[str] | None = None,
    directed: bool = False,
) -> list[EdgeView]:
    """Direct edges between two nodes. Aliases are resolved first."""
    a = resolve_id(db, a)
    b = resolve_id(db, b)
    if directed:
        where = "(src_id = ? AND dst_id = ?)"
        params: list[Any] = [a, b]
    else:
        where = "((src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?))"
        params = [a, b, b, a]
    if evidence_types:
        where += f" AND evidence_type IN ({_placeholders(len(evidence_types))})"
        params.extend(evidence_types)
    rows = db.conn.execute(
        f"SELECT {_EDGE_COLS} FROM edges WHERE {where}", params
    ).fetchall()
    return [_edge_view_from_row(r) for r in rows]


def find_paths(
    db: GraphDB,
    a: str,
    b: str,
    *,
    max_depth: int = 3,
    max_paths: int = 10,
    edge_kinds: Sequence[str] | None = None,
    evidence_types: Sequence[str] | None = None,
) -> PathsView:
    """BFS for paths from ``a`` to ``b`` up to ``max_depth`` hops.

    Treats the graph as **undirected** for traversal: we don't care whether
    money flowed donor->candidate or backwards, the relationship exists.
    Returns paths in non-decreasing length order. Stops once ``max_paths``
    have been collected.
    """
    if max_depth < 1:
        raise ValueError("max_depth must be >= 1")
    if max_paths < 1:
        raise ValueError("max_paths must be >= 1")

    a = resolve_id(db, a)
    b = resolve_id(db, b)
    if a == b:
        return PathsView()

    paths: list[list[PathHop]] = []
    nodes_seen: set[str] = {a, b}
    edges_seen: dict[str, EdgeView] = {}

    # State: (current_node, hops_so_far, visited_nodes_on_this_path)
    queue: deque[tuple[str, list[PathHop], frozenset[str]]] = deque()
    queue.append((a, [], frozenset([a])))

    while queue and len(paths) < max_paths:
        current, hops, visited = queue.popleft()
        if len(hops) >= max_depth:
            continue

        params: list[Any] = [current, current]
        where = "(src_id = ? OR dst_id = ?)"
        if edge_kinds:
            where += f" AND kind IN ({_placeholders(len(edge_kinds))})"
            params.extend(edge_kinds)
        if evidence_types:
            where += f" AND evidence_type IN ({_placeholders(len(evidence_types))})"
            params.extend(evidence_types)

        rows = db.conn.execute(
            f"SELECT {_EDGE_COLS} FROM edges WHERE {where}", params
        ).fetchall()
        for row in rows:
            other = row["dst_id"] if row["src_id"] == current else row["src_id"]
            if other in visited:
                continue
            edge_view = edges_seen.setdefault(row["id"], _edge_view_from_row(row))
            new_hop = PathHop(edge_id=edge_view.id, from_node=current, to_node=other)
            new_hops = [*hops, new_hop]
            if other == b:
                paths.append(new_hops)
                nodes_seen.update({h.from_node for h in new_hops})
                nodes_seen.update({h.to_node for h in new_hops})
                if len(paths) >= max_paths:
                    break
            else:
                queue.append((other, new_hops, visited | {other}))

    nodes = list(_fetch_nodes(db, nodes_seen).values())
    return PathsView(paths=paths, nodes=nodes, edges=list(edges_seen.values()))
