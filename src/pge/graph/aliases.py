"""Entity resolution: alias table + node merging.

Two operations:

* :func:`resolve_id` -- follow the alias chain to the canonical id.
* :func:`merge_nodes` -- merge ``from_id`` into ``to_id``: union external ids,
  rewrite all incident edges, record the alias, delete the source node.

Designed to be called explicitly, not as a side effect. Callers (source
ingest layers, the future Phase 5 ``dedupe`` pass) own when a merge happens.

Why merge into a single node instead of keeping both with an alias pointer?
Because every downstream query — neighbors, paths, aggregates — would have to
resolve through the alias on every hop. One physical node per logical entity
is simpler to reason about. The ``aliases`` row is for *backwards* lookup
("the FEC id you have used to point here").
"""

from __future__ import annotations

from pge.graph.db import GraphDB


def resolve_id(db: GraphDB, node_id: str, *, max_hops: int = 8) -> str:
    """Walk the alias chain to its terminus. Returns ``node_id`` unchanged when
    no alias exists. Bounded to defend against accidental cycles.
    """
    seen: set[str] = set()
    current = node_id
    for _ in range(max_hops):
        if current in seen:
            raise RuntimeError(f"alias cycle detected at {current}")
        seen.add(current)
        row = db.conn.execute(
            "SELECT to_id FROM aliases WHERE from_id = ?", (current,)
        ).fetchone()
        if row is None:
            return current
        current = row["to_id"]
    raise RuntimeError(f"alias chain too long starting at {node_id}")


def set_alias(
    db: GraphDB, from_id: str, to_id: str, *, source: str, confidence: str = "high"
) -> None:
    """Record that ``from_id`` is an alias of ``to_id``. Idempotent."""
    db.conn.execute(
        """
        INSERT INTO aliases(from_id, to_id, source, confidence) VALUES (?, ?, ?, ?)
        ON CONFLICT(from_id) DO UPDATE SET
            to_id = excluded.to_id,
            source = excluded.source,
            confidence = excluded.confidence
        """,
        (from_id, to_id, source, confidence),
    )


def merge_nodes(
    db: GraphDB,
    *,
    from_id: str,
    to_id: str,
    source: str,
    confidence: str = "high",
) -> None:
    """Merge ``from_id`` into ``to_id``.

    Steps (single transaction):
        1. Union ``external_ids`` from ``from_id`` into ``to_id``.
        2. Rewrite every edge whose ``src_id`` or ``dst_id`` is ``from_id``.
        3. Insert the alias row.
        4. Delete the ``from_id`` node row.

    Both nodes must exist. If they're the same id, this is a no-op.
    """
    if from_id == to_id:
        return

    # Disable FK for the duration: we're about to detach edges from from_id
    # and re-attach to to_id; doing it in two steps would otherwise trip the
    # FK on the intermediate state.
    with db.transaction():
        from_row = db.conn.execute(
            "SELECT id FROM nodes WHERE id = ?", (from_id,)
        ).fetchone()
        to_row = db.conn.execute("SELECT id FROM nodes WHERE id = ?", (to_id,)).fetchone()
        if from_row is None:
            raise ValueError(f"merge_nodes: source node {from_id} does not exist")
        if to_row is None:
            raise ValueError(f"merge_nodes: target node {to_id} does not exist")

        # 1. Reassign external_ids to the canonical node. (source, ext_id)
        # is the PK, so a given pair can only point at one node — by definition
        # from_id and to_id don't share any (source, ext_id), and a plain
        # UPDATE rebinds them safely.
        db.conn.execute(
            "UPDATE external_ids SET node_id = ? WHERE node_id = ?",
            (to_id, from_id),
        )

        # 2. Rewrite incident edges.
        db.conn.execute(
            "UPDATE edges SET src_id = ?, updated_at = datetime('now') WHERE src_id = ?",
            (to_id, from_id),
        )
        db.conn.execute(
            "UPDATE edges SET dst_id = ?, updated_at = datetime('now') WHERE dst_id = ?",
            (to_id, from_id),
        )

        # 3. Record the alias.
        set_alias(db, from_id, to_id, source=source, confidence=confidence)

        # 4. Drop the merged node.
        db.conn.execute("DELETE FROM nodes WHERE id = ?", (from_id,))


def find_node_by_external_id(db: GraphDB, source: str, ext_id: str) -> str | None:
    """Return the (resolved) node_id holding ``(source, ext_id)``, or None."""
    row = db.conn.execute(
        "SELECT node_id FROM external_ids WHERE source = ? AND ext_id = ?",
        (source, ext_id),
    ).fetchone()
    if row is None:
        return None
    return resolve_id(db, row["node_id"])
