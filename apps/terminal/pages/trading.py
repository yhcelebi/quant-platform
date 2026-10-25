"""Trading page: paper strategies, kill switch, last cycles, paper vs backtest (ADR 0015).
One of the terminal's two allowed writes: the kill switch (halt / resume). Nothing here can
place an order or approve live trading."""

from __future__ import annotations

from datetime import date

import streamlit as st
from sqlalchemy import select

from apps.terminal import charts
from apps.terminal.state import settings_or_stop
from qp.core import SystemClock, to_istanbul
from qp.platform import Database
from qp.reporting.paper import paper_vs_backtest
from qp.risk import gate
from qp.risk.models import HeartbeatRow, OrderRow
from qp.strategies.promotion import deployments

st.title("Trading")
st.caption("Paper only. Live trading needs the owner's approval and does not exist yet.")
settings = settings_or_stop()


def render(db: Database) -> None:
    global_halt = gate.halted(db, "-")
    c1, c2 = st.columns([3, 1])
    if global_halt:
        c1.error(f"GLOBAL HALT: {global_halt}")
        if c2.button("Resume all", type="secondary"):
            gate.set_halt(db, "global", halted_=False, reason="resumed from the terminal")
            st.rerun()
    else:
        c1.success("Trading enabled (paper)")
        if c2.button("HALT ALL", type="primary"):
            gate.set_halt(db, "global", halted_=True, reason="halted from the terminal")
            st.rerun()

    deps = deployments(db)
    if not deps:
        st.info("No strategies deployed: `uv run qp trader deploy ...` (runbook: trading.md).")
        return
    st.dataframe(
        [
            {
                "name": d.name,
                "signal": f"{d.signal} {d.params}",
                "market": d.instrument or f"{d.fx}/TRY",
                "status": d.status or "",
                "capital": f"{d.capital:,.2f}",
                "halted": gate.halted(db, d.name) or "",
            }
            for d in deps
        ],
        hide_index=True,
        width="stretch",
    )
    name = st.selectbox("Strategy", [d.name for d in deps])
    halted = gate.halted(db, name)
    if halted:
        st.warning(f"{name} halted: {halted}")
        if st.button(f"Resume {name}"):
            gate.set_halt(db, name, halted_=False, reason="resumed from the terminal")
            st.rerun()
    elif st.button(f"Halt {name}"):
        gate.set_halt(db, name, halted_=True, reason="halted from the terminal")
        st.rerun()

    report = paper_vs_backtest(settings, db, name, now=SystemClock().now())
    if report.days:
        days: list[date] = [d.day for d in report.days]
        charts.show(
            charts.lines(
                f"{name}: paper vs backtest ({report.weeks:.1f} weeks)",
                [
                    ("Paper", days, [float(d.paper) for d in report.days]),
                    ("Backtest", days, [float(d.backtest) for d in report.days]),
                ],
                y_format=",.0f",
            )
        )
        st.caption(
            f"Largest daily difference: {report.max_abs_diff:,.2f}. Exit criterion: 4+ weeks."
        )
    for n in report.notes:
        st.caption(n)
    with db.session() as s:
        dep = next(d for d in deps if d.name == name)
        orders = s.scalars(
            select(OrderRow)
            .where(OrderRow.strategy_id == dep.id)
            .order_by(OrderRow.id.desc())
            .limit(20)
        ).all()
        beat = s.scalar(
            select(HeartbeatRow.created_at)
            .where(HeartbeatRow.component == f"paper:{name}")
            .order_by(HeartbeatRow.id.desc())
        )
        rows = [
            {
                "bar": str(o.bar_date),
                "target": o.target,
                "quantity": f"{o.quantity}",
                "price": f"{o.price}",
                "accepted": "yes" if o.accepted else "no",
                "reasons": o.reasons,
            }
            for o in orders
        ]
    st.caption(
        f"Last heartbeat: {to_istanbul(beat):%Y-%m-%d %H:%M} Istanbul"
        if beat
        else "No heartbeat yet"
    )
    st.subheader("Recent orders")
    st.dataframe(rows, hide_index=True, width="stretch")


with Database.from_settings(settings.db) as database:
    try:
        deployments(database)
    except Exception as exc:  # show the reason instead of a stack trace
        st.error(f"Trading needs PostgreSQL (migrated): {type(exc).__name__}")
        st.stop()
    render(database)  # outside the try: st.rerun() must not be swallowed
