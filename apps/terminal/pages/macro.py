"""Macro page: indicators (point in time, transformed), revisions, the US Treasury curve and
the economic calendar (ADR 0012). Read-only."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import streamlit as st

from apps.terminal import charts
from apps.terminal.state import settings_or_stop
from qp.core import SystemClock, assume_istanbul, istanbul_date
from qp.core.errors import QPError
from qp.data.lake import CleanStore
from qp.macro.calendar import events
from qp.macro.curves import US_TREASURY, curve
from qp.macro.registry import indicator_history, load_registry
from qp.macro.transforms import Transform
from qp.macro.vintages import revisions
from qp.performance.service import series_data
from qp.platform.lake import LakePaths
from qp.platform.settings import default_config_dir

st.title("Macro")
settings = settings_or_stop()
now = SystemClock().now()
today = istanbul_date(now)
try:
    registry = load_registry(default_config_dir() / "indicators.yaml")
except QPError as exc:
    st.error(f"Indicator registry: {exc}")
    st.stop()
data = series_data(settings)

c1, c2, c3 = st.columns([2, 1, 1])
names = {f"{i.name} ({i.country})": i for i in registry.indicators}
ind = names[c1.selectbox("Indicator", list(names))]
transform = Transform(c2.selectbox("Transform", [t.value for t in Transform]))
known_day = c3.date_input(
    "As known on", value=today, help="Only data published by the end of this day"
)
as_of = assume_istanbul(datetime.combine(known_day, time(23, 59, 59)))

obs, raw = indicator_history(ind, data, until=known_day, as_of=as_of, transform=transform)
if obs:
    pct = transform not in (Transform.LEVEL, Transform.CHANGE, Transform.ROLLING_12)
    label = f"{ind.name}: {transform.value}" + ("" if pct else f" ({ind.unit})")
    charts.show(
        charts.lines(
            label,
            [(ind.name, [d for d, _ in obs], [v for _, v in obs])],
            y_format=".1%" if pct else "",
        )
    )
    st.caption(
        f"{len(raw)} observations from {ind.series}; latest {raw[-1].valid_date}, "
        f"known {raw[-1].known_time:%Y-%m-%d}. {ind.note}"
    )
    with st.expander("Revisions since an earlier day"):
        earlier_day = st.date_input("Earlier view", value=known_day - timedelta(days=90))
        earlier = assume_istanbul(datetime.combine(earlier_day, time(23, 59, 59)))
        revs = revisions(data, ind.series, until=known_day, earlier=earlier, later=as_of)
        st.dataframe(
            [
                {
                    "date": str(r.valid_date),
                    "before": "—" if r.before is None else f"{r.before}",
                    "after": "—" if r.after is None else f"{r.after}",
                    "change": "new" if r.change is None else f"{r.change}",
                }
                for r in revs
            ],
            hide_index=True,
        )
else:
    st.info(
        f"No data for {ind.series} known on {known_day}. "
        + (
            "Set the FRED key and run `uv run qp data fetch fred`."
            if ind.series.startswith("fred:")
            else "Needs the EVDS key and a confirmed code, or data/manual/series.csv."
        )
    )

st.subheader("US Treasury curve")
c = curve(US_TREASURY, data, known_day, as_of=as_of)
if c.points:
    charts.show(
        charts.curve(
            f"Constant-maturity yields, {known_day}",
            [p.years for p in c.points],
            [float(p.value) for p in c.points],
            [p.label for p in c.points],
        )
    )
    s1, s2 = st.columns(2)
    for col, (lo, sh) in zip((s1, s2), (("10Y", "2Y"), ("10Y", "3M")), strict=True):
        sp = c.spread(lo, sh)
        col.metric(f"{lo} - {sh}", "—" if sp is None else f"{sp:+.2f} pp")
else:
    st.info("No curve data yet (FRED key, then `uv run qp data fetch fred`).")

st.subheader("Economic calendar, next 30 days")
try:
    evs = events(
        CleanStore(LakePaths(settings.lake.root)),
        settings.manual.dir / "calendar.csv",
        today,
        today + timedelta(days=30),
        as_of=now,
    )
except QPError as exc:
    st.error(f"Calendar: {exc}")
    evs = []
if evs:
    st.dataframe(
        [
            {
                "day": str(e.day),
                "time": e.time or "—",
                "country": e.country,
                "event": e.title,
                "source": e.source,
            }
            for e in evs
        ],
        hide_index=True,
        width="stretch",
    )
else:
    st.caption(
        "No events: `uv run qp data fetch fred-calendar` (FRED key) or data/manual/calendar.csv."
    )
