"""SQLite graph storage layer.

Two tables: ``nodes`` and ``edges``. Both store the full Pydantic model as a
JSON ``payload`` column, with hot fields hoisted into typed columns so we can
index them.

The DB is wrapped in :class:`GraphDB`, a thin context-manager-friendly handle
that other layers depend on. Keeping this surface small is what makes a future
Postgres swap cheap — only this file should know we're on SQLite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = Path("data/pge.db")

# Bumped whenever the schema changes; ``init_db`` writes this into ``meta``
# so we can detect mismatches before they corrupt anything.
SCHEMA_VERSION = 3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    name          TEXT NOT NULL,
    payload       TEXT NOT NULL,           -- full Pydantic JSON
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);

CREATE TABLE IF NOT EXISTS edges (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    src_id         TEXT NOT NULL,
    dst_id         TEXT NOT NULL,
    evidence_type  TEXT NOT NULL,
    source_name    TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    as_of_date     TEXT,
    payload        TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(src_id) REFERENCES nodes(id),
    FOREIGN KEY(dst_id) REFERENCES nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_evidence ON edges(evidence_type);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_name, source_id);

CREATE TABLE IF NOT EXISTS external_ids (
    node_id   TEXT NOT NULL,
    source    TEXT NOT NULL,
    ext_id    TEXT NOT NULL,
    PRIMARY KEY (source, ext_id),
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_external_ids_node ON external_ids(node_id);

CREATE TABLE IF NOT EXISTS ingest_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Entity resolution: ``from_id`` was merged into ``to_id``. Lookups for
-- ``from_id`` should follow the chain to the canonical id.
CREATE TABLE IF NOT EXISTS aliases (
    from_id     TEXT PRIMARY KEY,
    to_id       TEXT NOT NULL,
    source      TEXT NOT NULL,
    confidence  TEXT NOT NULL DEFAULT 'high',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_aliases_to ON aliases(to_id);

-- Entity resolution review queue. Pair (a_id, b_id) is the candidate match;
-- ``status`` is one of: ``pending``, ``accepted`` (merged), ``rejected``
-- (recorded as a do-not-merge). ``decided_by`` and ``decided_at`` are
-- populated when a human acts on the pair. ``score`` is the model's
-- similarity (0.0-1.0). The ``a_id < b_id`` invariant is enforced by
-- :func:`pge.resolution.individuals.canonicalize_pair` so reverse-order
-- duplicates collapse onto the same row.
CREATE TABLE IF NOT EXISTS review_queue (
    a_id        TEXT NOT NULL,
    b_id        TEXT NOT NULL,
    score       REAL NOT NULL,
    features    TEXT NOT NULL,           -- JSON: per-field similarity breakdown
    status      TEXT NOT NULL DEFAULT 'pending',
    decided_by  TEXT,
    decided_at  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (a_id, b_id),
    CHECK (a_id < b_id),
    CHECK (status IN ('pending', 'accepted', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status);
CREATE INDEX IF NOT EXISTS idx_review_queue_score ON review_queue(score);
"""


class GraphDB:
    """Thin wrapper around a sqlite3 connection.

    Use as a context manager to get auto-commit / rollback semantics:

        with GraphDB.open(path) as db:
            db.upsert_node(...)
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    @classmethod
    def open(cls, path: Path | str = DEFAULT_DB_PATH) -> GraphDB:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(path))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> GraphDB:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def get_state(self, key: str) -> str | None:
        """Read a value from the ``ingest_state`` key/value table."""
        row = self.conn.execute("SELECT value FROM ingest_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        """Write a value into the ``ingest_state`` key/value table."""
        self.conn.execute(
            """
            INSERT INTO ingest_state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (key, value),
        )

    def stats(self) -> dict[str, int | dict[str, int]]:
        """Return high-level row counts grouped by kind."""
        cur = self.conn.cursor()
        node_total = cur.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_total = cur.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        nodes_by_kind = {
            row["kind"]: row["n"]
            for row in cur.execute("SELECT kind, COUNT(*) AS n FROM nodes GROUP BY kind")
        }
        edges_by_kind = {
            row["kind"]: row["n"]
            for row in cur.execute("SELECT kind, COUNT(*) AS n FROM edges GROUP BY kind")
        }
        edges_by_evidence = {
            row["evidence_type"]: row["n"]
            for row in cur.execute(
                "SELECT evidence_type, COUNT(*) AS n FROM edges GROUP BY evidence_type"
            )
        }
        return {
            "nodes_total": node_total,
            "edges_total": edge_total,
            "nodes_by_kind": nodes_by_kind,
            "edges_by_kind": edges_by_kind,
            "edges_by_evidence": edges_by_evidence,
        }


def init_db(path: Path | str = DEFAULT_DB_PATH) -> Path:
    """Create the DB file and apply the schema. Idempotent."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        conn.commit()
    return path
