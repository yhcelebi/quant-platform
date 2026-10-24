"""Research page: the study registry, each study's runs and their provenance (ADR 0013).
Read-only: run studies with `uv run qp research run ID`."""

from __future__ import annotations

import json

import streamlit as st

from apps.terminal.state import settings_or_stop
from qp.core.errors import QPError
from qp.reporting.studies import list_studies, read_run

st.title("Research")
settings = settings_or_stop()
try:
    studies = list_studies(settings)
except QPError as exc:
    st.error(f"Study registry: {exc}")
    st.stop()
if not studies:
    st.info("No studies yet. Copy research/templates/study to research/studies/<id>/.")
    st.stop()

st.dataframe(
    [
        {
            "study": s.study_id,
            "title": s.spec.title,
            "as_of": s.spec.as_of.isoformat(),
            "runs": len(s.runs),
            "tags": ", ".join(s.spec.tags),
        }
        for s in studies
    ],
    hide_index=True,
    width="stretch",
)
chosen = st.selectbox("Study", [s.study_id for s in studies])
info = next(s for s in studies if s.study_id == chosen)
st.markdown(f"**{info.spec.title}**")
st.markdown(f"Hypothesis: {info.spec.hypothesis}")
if not info.runs:
    st.info(f"Not run yet: `uv run qp research run {chosen}`")
    st.stop()
run_name = st.selectbox("Run", [p.name for p in reversed(info.runs)])
run = read_run(next(p for p in info.runs if p.name == run_name))
p = run.provenance
c1, c2, c3 = st.columns(3)
c1.metric("Runs of this study", len(info.runs), help="Count every run as a trial (deflated Sharpe)")
c2.metric("Commit", (p.get("git_commit") or "—")[:10])
c3.metric("Seed", p.get("seed"))
if p.get("git_dirty"):
    st.warning("Run from uncommitted code: not reproducible from the commit alone.")
st.subheader("Result")
st.json(run.result)
with st.expander("Provenance"):
    st.code(json.dumps(p, indent=2), language="json")
