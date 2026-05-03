"""Idempotent upsert helpers.

These are the only functions that should write to ``nodes`` / ``edges`` from
outside the ``graph`` package. Source-specific ``to_graph.py`` modules build
typed Pydantic objects and hand them here.

Re-running ingest with the same input must produce the same DB state — that's
what makes incremental loads safe. We achieve this with ``ON CONFLICT`` on the
primary key plus deterministic ids supplied by callers.
"""

from __future__ import annotations

from pge.graph.db import GraphDB
from pge.schema.edges import _EdgeBase
from pge.schema.nodes import _NodeBase


def upsert_node(db: GraphDB, node: _NodeBase) -> None:
    """Insert or update a node by ``id``. Also syncs its ``external_ids``."""
    payload = node.model_dump_json()
    db.conn.execute(
        """
        INSERT INTO nodes(id, kind, name, payload)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind = excluded.kind,
            name = excluded.name,
            payload = excluded.payload,
            updated_at = datetime('now')
        """,
        (node.id, node.kind, node.name, payload),
    )
    # Keep external_ids in sync. We delete-then-insert because the set may
    # shrink across runs (rarely, but possible).
    db.conn.execute("DELETE FROM external_ids WHERE node_id = ?", (node.id,))
    if node.external_ids:
        db.conn.executemany(
            "INSERT OR IGNORE INTO external_ids(node_id, source, ext_id) VALUES (?, ?, ?)",
            [(node.id, source, ext_id) for source, ext_id in node.external_ids.items()],
        )


def upsert_edge(db: GraphDB, edge: _EdgeBase) -> None:
    """Insert or update an edge by ``id``.

    Does not enforce that ``src_id`` / ``dst_id`` exist beyond the SQLite
    foreign key (which we leave deferred-by-default). Caller is responsible
    for inserting endpoints first when relevant.
    """
    payload = edge.model_dump_json()
    as_of = edge.as_of_date.isoformat() if edge.as_of_date else None
    db.conn.execute(
        """
        INSERT INTO edges(
            id, kind, src_id, dst_id, evidence_type,
            source_name, source_id, as_of_date, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind = excluded.kind,
            src_id = excluded.src_id,
            dst_id = excluded.dst_id,
            evidence_type = excluded.evidence_type,
            source_name = excluded.source_name,
            source_id = excluded.source_id,
            as_of_date = excluded.as_of_date,
            payload = excluded.payload,
            updated_at = datetime('now')
        """,
        (
            edge.id,
            edge.kind,
            edge.src_id,
            edge.dst_id,
            edge.evidence_type,
            edge.source_name,
            edge.source_id,
            as_of,
            payload,
        ),
    )


def get_node_payload(db: GraphDB, node_id: str) -> tuple[str, dict] | None:
    """Fetch a node's ``(kind, payload)`` for reconstruction. None if missing."""
    import json

    row = db.conn.execute("SELECT kind, payload FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        return None
    return row["kind"], json.loads(row["payload"])


def get_edge_payload(db: GraphDB, edge_id: str) -> tuple[str, dict] | None:
    """Fetch an edge's ``(kind, payload)`` for reconstruction. None if missing."""
    import json

    row = db.conn.execute("SELECT kind, payload FROM edges WHERE id = ?", (edge_id,)).fetchone()
    if row is None:
        return None
    return row["kind"], json.loads(row["payload"])
