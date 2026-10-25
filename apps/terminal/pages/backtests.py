"""Backtests page: stored runs (NautilusTrader), equity against buy and hold, walk-forward
windows with deflated Sharpe and PBO (ADR 0014). Read-only: run with `qp backtest`."""

from __future__ import annotations

from datetime import date

import streamlit as st

from apps.terminal import charts
from apps.terminal.state import settings_or_stop
from qp.backtest.results import list_runs, read_equity, read_summary

st.title("Backtests")
settings = settings_or_stop()
runs = list_runs(settings)
if not runs:
    st.info(
        "No backtests yet: `uv run qp backtest run --fx USD --signal sma_cross --costs fx_try`."
    )
    st.stop()


def pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.2%}"


rows = []
for folder in reversed(runs):
    s = read_summary(folder)
    m = s["metrics"] or {}
    rows.append(
        {
            "run": f"{folder.parent.name}/{folder.name}",
            "kind": s["kind"],
            "signal": s["spec"]["signal"],
            "instrument": s["spec"]["instrument"],
            "valid": "yes" if s["valid"] else "no (costless)",
            "superseded": "yes" if s.get("superseded") else "",
            "total return": pct(m.get("total_return")),
            "Sharpe": "—" if m.get("sharpe") is None else f"{m['sharpe']:.2f}",
        }
    )
st.dataframe(rows, hide_index=True, width="stretch")
choice = st.selectbox("Run", [r["run"] for r in rows])
folder = next(f for f in runs if f"{f.parent.name}/{f.name}" == choice)
s = read_summary(folder)
if s.get("superseded"):
    st.error(s["superseded"])
if not s["valid"]:
    st.error(s["invalid_reason"])
m = s["metrics"] or {}
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total return", pct(m.get("total_return")))
c2.metric("Sharpe", "—" if m.get("sharpe") is None else f"{m['sharpe']:.2f}")
c3.metric("Max drawdown", pct(m.get("max_drawdown")))
c4.metric("Trades / fees", f"{m.get('trades', 0)} / {m.get('fees', 0):,.2f}")
st.caption(
    f"{s['spec']['signal']} {s['spec'].get('params', '')} on {s['spec']['instrument']} "
    f"({s['spec']['source']}), costs '{s['spec']['costs']}', lag {s['spec']['lag_bars']} bar, "
    f"commit {(s['provenance']['git_commit'] or '')[:10]}"
    + (" (uncommitted changes)" if s["provenance"]["git_dirty"] else "")
)

equity = read_equity(folder)
if equity:
    days = [date.fromisoformat(r["day"]) for r in equity]
    eq = [float(r["equity"]) for r in equity]
    charts.show(charts.lines("Equity", [("Strategy", days, eq)], y_format=",.0f"))
wf = s.get("walk_forward")
if wf:
    st.subheader("Walk-forward")
    w1, w2, w3 = st.columns(3)
    w1.metric("Trials (grid)", len(wf["grid"]))
    w2.metric("Deflated Sharpe (prob.)", pct(wf["dsr"]))
    w3.metric(
        "PBO",
        pct(wf["pbo"]),
        help="Probability that the in-sample winner is below median out of sample",
    )
    st.dataframe(
        [
            {
                "train": f"{w['train'][0]}..{w['train'][1]}",
                "test": f"{w['test'][0]}..{w['test'][1]}",
                "chosen": str(w["params"]),
                "train Sharpe": "—" if w["train_sharpe"] is None else f"{w['train_sharpe']:.2f}",
                "test return": pct(w["test_return"]),
            }
            for w in wf["windows"]
        ],
        hide_index=True,
        width="stretch",
    )
    for n in wf["notes"]:
        st.caption(n)
with st.expander("Provenance"):
    st.json(s["provenance"])
