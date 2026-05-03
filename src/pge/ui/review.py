"""Streamlit review UI for ambiguous Individual-resolution pairs.

Run via ``pge ui review`` (which sets ``PGE_DB_PATH`` and shells out to
``streamlit run``). The UI loads pending pairs from ``review_queue``, shows
both records side-by-side with the per-field similarity breakdown, and lets
the user accept (merge), reject (record as do-not-merge), or skip.

We **don't** call ``merge_nodes`` from here. Accepting just records the
decision; the merge is performed by ``pge resolve individuals`` on the next
run (which calls :func:`apply_decisions`). That keeps the UI dumb and the
heavy lifting in one tested place.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from pge.graph.db import GraphDB
from pge.resolution.individuals import (
    list_pending_review,
    record_decision,
)


def _db_path() -> Path:
    return Path(os.environ.get("PGE_DB_PATH", "data/pge.db"))


def _render_record(label: str, rec: dict) -> None:
    st.markdown(f"### {label}")
    st.caption(rec.get("id", ""))
    st.write(f"**Name:** {rec.get('name', '')}")
    if rec.get("employer"):
        st.write(f"**Employer:** {rec['employer']}")
    if rec.get("occupation"):
        st.write(f"**Occupation:** {rec['occupation']}")
    if rec.get("external_ids"):
        st.write("**external_ids:**")
        st.json(rec["external_ids"], expanded=False)
    with st.expander("raw payload", expanded=False):
        st.json(rec)


def main() -> None:
    st.set_page_config(page_title="PGE — Individual Review", layout="wide")
    st.title("Entity Resolution — Individual review queue")

    path = _db_path()
    if not path.exists():
        st.error(f"No DB at `{path}`. Run `pge db init` and at least one ingest first.")
        return

    with GraphDB.open(path) as db:
        st.sidebar.header("Filters")
        min_score = st.sidebar.slider(
            "Min score", min_value=0.5, max_value=1.0, value=0.8, step=0.01
        )
        limit = st.sidebar.number_input("Limit", min_value=1, max_value=200, value=25)
        pending = list_pending_review(db, limit=int(limit), min_score=min_score)

    if not pending:
        st.success(
            "No pending pairs. Run `pge resolve individuals` to populate the queue."
        )
        return

    st.caption(
        f"{len(pending)} pending pair(s). Decisions are saved immediately and "
        "applied by the next `pge resolve individuals` run."
    )

    for i, item in enumerate(pending):
        a, b = item["a"], item["b"]
        st.divider()
        c1, c2, c3 = st.columns([3, 3, 2])
        with c1:
            _render_record("A", a)
        with c2:
            _render_record("B", b)
        with c3:
            st.metric("Score", f"{item['score']:.3f}")
            st.write("**Field similarity:**")
            for field, value in item["features"].items():
                st.progress(min(max(value, 0.0), 1.0), text=f"{field}: {value:.2f}")
            cols = st.columns(3)
            decision_key = f"dec-{a['id']}-{b['id']}"
            if cols[0].button("Merge", key=f"merge-{i}"):
                with GraphDB.open(_db_path()) as db:
                    record_decision(db, a["id"], b["id"], "accepted")
                st.session_state[decision_key] = "accepted"
                st.rerun()
            if cols[1].button("Reject", key=f"reject-{i}"):
                with GraphDB.open(_db_path()) as db:
                    record_decision(db, a["id"], b["id"], "rejected")
                st.session_state[decision_key] = "rejected"
                st.rerun()
            cols[2].caption("(or skip — closing leaves it pending.)")


if __name__ == "__main__":
    main()
