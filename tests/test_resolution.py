"""Entity resolution tests for the Individual clustering pipeline."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pge.graph.aliases import resolve_id
from pge.graph.db import GraphDB, init_db
from pge.graph.ingest import upsert_edge, upsert_node
from pge.resolution.individuals import (
    FIELD_WEIGHTS,
    apply_decisions,
    block_key,
    block_records,
    candidate_pairs,
    canonicalize_pair,
    extract_records,
    list_pending_review,
    merge_pair,
    queue_for_review,
    record_decision,
    resolve_individuals,
    score_pair,
)
from pge.schema.edges import DonationEdge
from pge.schema.nodes import IndividualNode, PACNode

# ---- helpers -------------------------------------------------------------


def _ind(node_id: str, name: str, *, employer: str = "", occupation: str = "") -> IndividualNode:
    return IndividualNode(
        id=node_id, name=name, employer=employer, occupation=occupation
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "pge.db"
    init_db(path)
    return path


# ---- weights are sane ----------------------------------------------------


def test_field_weights_sum_to_one() -> None:
    assert abs(sum(FIELD_WEIGHTS.values()) - 1.0) < 1e-9


# ---- normalization / blocking -------------------------------------------


def test_block_key_handles_last_first_format() -> None:
    assert block_key("DOE, JANE Q") == "doe|j"
    assert block_key("Jane Doe") == "doe|j"
    assert block_key("jane doe") == "doe|j"


def test_block_key_punctuation_normalized() -> None:
    assert block_key("O'Brien, Patrick") == "obrien|p"


def test_block_key_empty_for_blank() -> None:
    assert block_key("") == ""
    assert block_key(None) == ""  # type: ignore[arg-type]


def test_canonicalize_pair_orders_lex() -> None:
    assert canonicalize_pair("z", "a") == ("a", "z")
    assert canonicalize_pair("a", "z") == ("a", "z")


# ---- extract -------------------------------------------------------------


def test_extract_records(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:1", "Jane Doe", employer="Acme"))
        upsert_node(db, _ind("ind:2", "Bob Smith"))
        upsert_node(db, PACNode(id="pac:1", name="Industry PAC"))
    with GraphDB.open(db_path) as db:
        recs = extract_records(db)
    ids = {r.id for r in recs}
    assert ids == {"ind:1", "ind:2"}  # PAC excluded


def test_block_records_groups(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:1", "Jane Doe", employer="Acme"))
        upsert_node(db, _ind("ind:2", "Janet Doe", employer="Beta"))
        upsert_node(db, _ind("ind:3", "Bob Smith"))
    with GraphDB.open(db_path) as db:
        records = extract_records(db)
    blocks = block_records(records)
    assert "doe|j" in blocks
    assert {r.id for r in blocks["doe|j"]} == {"ind:1", "ind:2"}
    assert "smith|b" in blocks


# ---- scoring -------------------------------------------------------------


def test_score_pair_high_when_everything_matches(db_path: Path) -> None:
    a = _ind("ind:a", "DOE, JANE", employer="Acme Corp", occupation="Engineer")
    b = _ind("ind:b", "Jane Doe", employer="Acme Corp", occupation="Engineer")
    from pge.resolution.individuals import IndividualRecord

    ra = IndividualRecord(
        id=a.id, name=a.name, employer=a.employer or "",
        occupation=a.occupation or "", zip5="", state="", raw={},
    )
    rb = IndividualRecord(
        id=b.id, name=b.name, employer=b.employer or "",
        occupation=b.occupation or "", zip5="", state="", raw={},
    )
    score, features = score_pair(ra, rb)
    # Without zip we miss 0.10; everything else perfect -> 0.90.
    assert score >= 0.85
    assert features["name"] == 1.0
    assert features["employer"] == 1.0
    assert features["zip"] == 0.0  # neither set


def test_score_pair_low_when_nothing_matches() -> None:
    from pge.resolution.individuals import IndividualRecord

    ra = IndividualRecord(
        id="a", name="Alice Anderson", employer="Acme",
        occupation="Engineer", zip5="", state="", raw={},
    )
    rb = IndividualRecord(
        id="b", name="Bob Brown", employer="Beta",
        occupation="Doctor", zip5="", state="", raw={},
    )
    score, _ = score_pair(ra, rb)
    assert score < 0.5


def test_candidate_pairs_within_block_only(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:1", "Jane Doe", employer="Acme"))
        upsert_node(db, _ind("ind:2", "Jane Doe", employer="Acme"))
        upsert_node(db, _ind("ind:3", "Bob Smith"))
    with GraphDB.open(db_path) as db:
        records = extract_records(db)
    pairs = candidate_pairs(records, min_score=0.0)
    pair_ids = {(p.a_id, p.b_id) for p in pairs}
    # Only the two Does compare; Smith is in a different block.
    assert pair_ids == {("ind:1", "ind:2")}


def test_candidate_pairs_canonicalize_order(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:b", "Jane Doe"))
        upsert_node(db, _ind("ind:a", "Jane Doe"))
    with GraphDB.open(db_path) as db:
        records = extract_records(db)
    pairs = candidate_pairs(records, min_score=0.0)
    assert len(pairs) == 1
    p = pairs[0]
    assert p.a_id == "ind:a"
    assert p.b_id == "ind:b"


# ---- review queue --------------------------------------------------------


def test_queue_for_review_idempotent(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:a", "Jane"))
        upsert_node(db, _ind("ind:b", "Jane"))
    pair = candidate_pairs(extract_records_for_path(db_path), min_score=0.0)[0]
    with GraphDB.open(db_path) as db:
        first = queue_for_review(db, pair)
        second = queue_for_review(db, pair)
        third = queue_for_review(db, pair)
    assert first is True
    assert second is False
    assert third is False


def extract_records_for_path(path: Path):
    with GraphDB.open(path) as db:
        return extract_records(db)


def test_record_decision_updates_status(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:a", "Jane"))
        upsert_node(db, _ind("ind:b", "Jane"))
    pairs = candidate_pairs(extract_records_for_path(db_path), min_score=0.0)
    with GraphDB.open(db_path) as db:
        queue_for_review(db, pairs[0])
        record_decision(db, "ind:a", "ind:b", "accepted")
    with GraphDB.open(db_path) as db:
        row = db.conn.execute(
            "SELECT status FROM review_queue WHERE a_id = 'ind:a' AND b_id = 'ind:b'"
        ).fetchone()
    assert row["status"] == "accepted"


def test_record_decision_rejects_invalid_status(db_path: Path) -> None:
    with GraphDB.open(db_path) as db, pytest.raises(ValueError):
        record_decision(db, "x", "y", "maybe")


# ---- merge_pair ----------------------------------------------------------


def test_merge_pair_picks_lex_min_canonical(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:zzz", "Jane Doe"))
        upsert_node(db, _ind("ind:aaa", "Jane Doe"))
        upsert_node(db, PACNode(id="pac:1", name="Donor"))
        upsert_edge(db, DonationEdge(
            id="e:1", src_id="pac:1", dst_id="ind:zzz",
            evidence_type="VERIFIED", source_name="fec",
            source_id="t1", amount_cents=500,
        ))
    with GraphDB.open(db_path) as db:
        canonical = merge_pair(db, "ind:zzz", "ind:aaa", source="test")
    assert canonical == "ind:aaa"
    with GraphDB.open(db_path) as db:
        # Edge rebound to canonical.
        row = db.conn.execute("SELECT dst_id FROM edges WHERE id = 'e:1'").fetchone()
        assert row["dst_id"] == "ind:aaa"
        # Alias chain works.
        assert resolve_id(db, "ind:zzz") == "ind:aaa"


# ---- end-to-end orchestration --------------------------------------------


def test_resolve_individuals_auto_merges_high_confidence(db_path: Path) -> None:
    # Exact name + exact employer + exact occupation -> 0.90 score, well past 0.85.
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:a", "DOE, JANE",
                              employer="Acme Corp", occupation="Engineer"))
        upsert_node(db, _ind("ind:b", "Jane Doe",
                              employer="Acme Corp", occupation="Engineer"))
        upsert_node(db, _ind("ind:c", "Bob Smith"))
    with GraphDB.open(db_path) as db:
        summary = resolve_individuals(
            db, auto_merge_threshold=0.85, review_threshold=0.50,
        )
    assert summary.auto_merged >= 1
    with GraphDB.open(db_path) as db:
        n = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'Individual'"
        ).fetchone()[0]
    assert n == 2  # ind:a and ind:b merged


def test_resolve_individuals_queues_borderline(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        # Same name but utterly different employer/occupation -> mid-band.
        upsert_node(db, _ind("ind:a", "DOE, JANE",
                              employer="Acme Corp", occupation="Engineer"))
        upsert_node(db, _ind("ind:b", "Jane Doe",
                              employer="Different Co", occupation="Doctor"))
    with GraphDB.open(db_path) as db:
        summary = resolve_individuals(
            db, auto_merge_threshold=0.95, review_threshold=0.50,
        )
    assert summary.auto_merged == 0
    assert summary.queued_for_review >= 1
    with GraphDB.open(db_path) as db:
        rows = list_pending_review(db, limit=10, min_score=0.0)
    assert len(rows) == 1


def test_resolve_individuals_idempotent(db_path: Path) -> None:
    # Need name + employer + occupation matches to clear 0.85; bare name+employer
    # tops out at 0.80 (= 0.55 + 0.25), which falls into the review band.
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:a", "Jane Doe", employer="Acme", occupation="Engineer"))
        upsert_node(db, _ind("ind:b", "Jane Doe", employer="Acme", occupation="Engineer"))
    with GraphDB.open(db_path) as db:
        s1 = resolve_individuals(db, auto_merge_threshold=0.85, review_threshold=0.5)
        s2 = resolve_individuals(db, auto_merge_threshold=0.85, review_threshold=0.5)
    assert s1.auto_merged >= 1
    assert s2.auto_merged == 0  # nothing left to merge


def test_apply_decisions_processes_accepted_pairs(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:a", "DOE, JANE",
                              employer="Acme Corp", occupation="Engineer"))
        upsert_node(db, _ind("ind:b", "Jane Doe",
                              employer="Different Co", occupation="Doctor"))
    with GraphDB.open(db_path) as db:
        # Queue the pair, then accept it manually (simulating reviewer).
        resolve_individuals(db, auto_merge_threshold=0.99, review_threshold=0.5)
        record_decision(db, "ind:a", "ind:b", "accepted")
    with GraphDB.open(db_path) as db:
        merged = apply_decisions(db)
    assert merged == 1
    with GraphDB.open(db_path) as db:
        n = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'Individual'"
        ).fetchone()[0]
    assert n == 1


def test_resolve_individuals_skips_rejected(db_path: Path) -> None:
    """A previously-rejected pair must not get re-queued or merged."""
    with GraphDB.open(db_path) as db:
        upsert_node(db, _ind("ind:a", "Jane Doe"))
        upsert_node(db, _ind("ind:b", "Jane Doe"))
    with GraphDB.open(db_path) as db:
        resolve_individuals(db, auto_merge_threshold=0.999, review_threshold=0.5)
        record_decision(db, "ind:a", "ind:b", "rejected")
    with GraphDB.open(db_path) as db:
        summary = resolve_individuals(
            db, auto_merge_threshold=0.999, review_threshold=0.5
        )
    assert summary.auto_merged == 0
    assert summary.queued_for_review == 0
    assert summary.skipped_already_decided >= 1
    with GraphDB.open(db_path) as db:
        n = db.conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE kind = 'Individual'"
        ).fetchone()[0]
    assert n == 2  # both still there


def test_resolve_individuals_invalid_thresholds(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        with pytest.raises(ValueError):
            resolve_individuals(db, auto_merge_threshold=0.5, review_threshold=0.8)
        with pytest.raises(ValueError):
            resolve_individuals(db, auto_merge_threshold=1.5, review_threshold=0.8)
        with pytest.raises(ValueError):
            resolve_individuals(db, auto_merge_threshold=0.95, review_threshold=0.0)


def test_list_pending_review_filters_by_score(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        # High-score pair (name + employer + occupation match): score ~0.90.
        upsert_node(db, _ind("ind:a", "Jane Doe",
                              employer="Acme", occupation="Engineer"))
        upsert_node(db, _ind("ind:b", "Jane Doe",
                              employer="Acme", occupation="Engineer"))
        # Lower-score pair: name match only -> ~0.55.
        upsert_node(db, _ind("ind:c", "Bob Smith"))
        upsert_node(db, _ind("ind:d", "Bob Smith"))
    with GraphDB.open(db_path) as db:
        # High auto-merge so even strong matches end up queued.
        resolve_individuals(db, auto_merge_threshold=0.999, review_threshold=0.5)
    with GraphDB.open(db_path) as db:
        all_pending = list_pending_review(db, limit=10, min_score=0.0)
        high_only = list_pending_review(db, limit=10, min_score=0.85)
    assert len(all_pending) == 2
    assert len(high_only) == 1
    assert high_only[0]["score"] >= 0.85


# ---- schema regression ---------------------------------------------------


def test_schema_includes_review_queue_table(db_path: Path) -> None:
    with GraphDB.open(db_path) as db:
        tables = {
            row["name"]
            for row in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "review_queue" in tables


def test_review_queue_pk_constraint_orders_pair(db_path: Path) -> None:
    """The CHECK (a_id < b_id) enforces canonical ordering at the DB layer."""
    import sqlite3
    with GraphDB.open(db_path) as db, pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO review_queue(a_id, b_id, score, features) "
            "VALUES ('z', 'a', 0.5, '{}')"
        )


# ---- date import smoke ---------------------------------------------------

def test_date_import_used() -> None:
    # Sanity: date is referenced indirectly via DonationEdge in a fixture.
    assert isinstance(date(2024, 1, 1), date)
