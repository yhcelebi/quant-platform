"""Data page: what is in the lake, how fresh it is, and whether it passed quality checks.
Read-only."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from qp.core import SystemClock, istanbul_date, to_istanbul
from qp.core.errors import QPError
from qp.data import service
from qp.platform import Database, init_platform
from qp.platform.jobs import recent_runs

st.title("Data")
try:
    settings = init_platform()
except QPError as exc:
    st.error(f"Configuration error: {exc}")
    st.stop()

now = SystemClock().now()
st.subheader("Lake")
st.dataframe(
    [
        {
            "dataset": d.name,
            "rows": d.rows,
            "first": str(d.first_valid or "—"),
            "last": str(d.last_valid or "—"),
            "last fetched (Istanbul)": (
                to_istanbul(d.last_fetched).strftime("%Y-%m-%d %H:%M") if d.last_fetched else "—"
            ),
        }
        for d in service.dataset_status(settings)
    ],
    width="stretch",
)

days = st.slider("FX history (days)", 30, 730, 180, step=30)
history = service.fx_history(
    settings, ("USD", "EUR"), istanbul_date(now) - timedelta(days=days), as_of=now
)
if any(history.values()):
    kind = settings.sources.tcmb.rate_kind
    st.caption(f"TCMB indicative rates ({kind}), TRY per unit. Display only.")
    for code, series in history.items():
        if series:
            # float conversion for the chart only; stored values stay exact Decimals
            st.line_chart(
                {"date": [d for d, _ in series], f"{code}/TRY": [float(v) for _, v in series]},
                x="date",
                y=f"{code}/TRY",
            )
else:
    st.info("No TCMB rates in the lake yet: `uv run qp data fetch tcmb --since 2025-01-01`.")

try:
    with Database.from_settings(settings.db) as db:
        runs = recent_runs(db, 15)
        quality = service.latest_quality(db)
except Exception as exc:  # the lake view above still works without a database
    st.warning(f"Job runs and quality results need PostgreSQL: {type(exc).__name__}")
    st.stop()

st.subheader("Latest quality checks")
if quality:
    bad = [q for q in quality if q.status != "ok"]
    if bad:
        st.warning(f"{len(bad)} of {len(quality)} checks are not ok")
    else:
        st.success(f"all {len(quality)} checks ok")
    st.dataframe(
        [
            {
                "dataset": q.dataset,
                "subject": q.subject,
                "check": q.check_name,
                "status": q.status,
                "detail": q.detail,
            }
            for q in quality
        ],
        width="stretch",
    )
else:
    st.info("No quality run yet: `uv run qp data quality`.")

st.subheader("Recent job runs")
st.dataframe(
    [
        {
            "id": r.id,
            "job": r.job,
            "started (Istanbul)": to_istanbul(r.started_at).strftime("%Y-%m-%d %H:%M"),
            "status": r.status,
            "detail": r.detail,
        }
        for r in runs
    ],
    width="stretch",
)
