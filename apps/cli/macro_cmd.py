"""``qp macro``: indicators, revisions, yield curve and the economic calendar (ADR 0012)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from apps.cli.common import console, fail, settings
from qp.core import SystemClock, istanbul_date, parse_utc
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

app = typer.Typer(no_args_is_help=True, help="Macro: indicators, vintages, curves, calendar.")
KnownAt = Annotated[str | None, typer.Option(help="As known at this ISO time (default: now)")]


@app.command("indicators")
def indicators() -> None:
    """The indicator registry (configs/indicators.yaml) with the latest value of each."""
    cfg = settings()
    now = SystemClock().now()
    try:
        reg = load_registry(default_config_dir() / "indicators.yaml")
        data = series_data(cfg)
        t = Table("id", "name", "country", "category", "latest", "date", "series")
        for ind in reg.indicators:
            obs, _ = indicator_history(ind, data, until=istanbul_date(now), as_of=now)
            last = obs[-1] if obs else None
            t.add_row(
                ind.id,
                ind.name,
                ind.country,
                ind.category,
                f"{last[1]:,.4g} {ind.unit}" if last else "[dim]no data[/dim]",
                str(last[0]) if last else "—",
                ind.series,
            )
    except QPError as exc:
        fail(str(exc))
    console.print(t)


@app.command("show")
def show(
    indicator: Annotated[str, typer.Argument(help="Indicator id (qp macro indicators)")],
    transform: Annotated[Transform, typer.Option(help="Transformation")] = Transform.LEVEL,
    last: Annotated[int, typer.Option(help="Observations to list")] = 24,
    known_at: KnownAt = None,
) -> None:
    """An indicator's history, transformed, as known at a moment."""
    cfg = settings()
    as_of = parse_utc(known_at) if known_at else SystemClock().now()
    try:
        ind = load_registry(default_config_dir() / "indicators.yaml").get(indicator)
        obs, raw = indicator_history(
            ind, series_data(cfg), until=istanbul_date(as_of), as_of=as_of, transform=transform
        )
    except QPError as exc:
        fail(str(exc))
    if not obs:
        console.print(f"no data for {ind.series} known at {as_of:%Y-%m-%d %H:%M} UTC")
        raise typer.Exit(code=1)
    pct = transform not in (Transform.LEVEL, Transform.CHANGE, Transform.ROLLING_12)
    t = Table("date", f"{ind.name} ({transform.value})", title=f"{ind.id}, source {ind.series}")
    for d, v in obs[-last:]:
        t.add_row(str(d), f"{v:.2%}" if pct else f"{v:,.4f}")
    console.print(t)
    console.print(
        f"{len(raw)} observations from {raw[0].source}; latest known {raw[-1].known_time:%Y-%m-%d}"
    )


@app.command("revisions")
def show_revisions(
    indicator: Annotated[str, typer.Argument(help="Indicator id")],
    earlier: Annotated[str, typer.Option(help="ISO time of the earlier view")],
    later: KnownAt = None,
) -> None:
    """What changed in a series between two moments (revisions and new releases)."""
    cfg = settings()
    t2 = parse_utc(later) if later else SystemClock().now()
    try:
        ind = load_registry(default_config_dir() / "indicators.yaml").get(indicator)
        revs = revisions(
            series_data(cfg),
            ind.series,
            until=istanbul_date(t2),
            earlier=parse_utc(earlier),
            later=t2,
        )
    except QPError as exc:
        fail(str(exc))
    t = Table(
        "date", "before", "after", "change", title=f"{ind.id}: {earlier} → {t2:%Y-%m-%d %H:%M}"
    )
    for r in revs:
        t.add_row(
            str(r.valid_date), str(r.before or "—"), str(r.after or "—"), str(r.change or "new")
        )
    console.print(t)


@app.command("curve")
def show_curve(
    day: Annotated[str | None, typer.Option(help="Day YYYY-MM-DD (default: today)")] = None,
    known_at: KnownAt = None,
) -> None:
    """US Treasury curve (FRED constant maturities) and spreads."""
    cfg = settings()
    as_of = parse_utc(known_at) if known_at else SystemClock().now()
    d = date.fromisoformat(day) if day else istanbul_date(as_of)
    c = curve(US_TREASURY, series_data(cfg), d, as_of=as_of)
    if not c.points:
        console.print("no curve data (FRED key and `uv run qp data fetch fred` needed)")
        raise typer.Exit(code=1)
    t = Table("tenor", "yield %", "date", title=f"US Treasury curve {d}")
    for p in c.points:
        t.add_row(p.label, f"{p.value:.2f}", f"{p.valid_date}{' (stale)' if p.stale else ''}")
    console.print(t)
    for long, short in (("10Y", "2Y"), ("10Y", "3M")):
        s = c.spread(long, short)
        console.print(f"{long}-{short}: {'—' if s is None else f'{s:+.2f} pp'}")
    if c.missing:
        console.print(f"missing tenors: {', '.join(c.missing)}")


@app.command("calendar")
def calendar(
    days: Annotated[int, typer.Option(help="Days ahead")] = 14,
    country: Annotated[list[str] | None, typer.Option(help="Country code (repeatable)")] = None,
) -> None:
    """Upcoming releases: FRED release dates and data/manual/calendar.csv."""
    cfg = settings()
    now = SystemClock().now()
    today = istanbul_date(now)
    try:
        evs = events(
            CleanStore(LakePaths(cfg.lake.root)),
            cfg.manual.dir / "calendar.csv",
            today,
            today + timedelta(days=days),
            as_of=now,
            countries=set(country) if country else None,
        )
    except QPError as exc:
        fail(str(exc))
    t = Table("day", "time", "country", "event", "category", "source")
    for e in evs:
        t.add_row(str(e.day), e.time or "—", e.country, escape(e.title), e.category, e.source)
    console.print(t)
    if not evs:
        console.print(
            "no events: `uv run qp data fetch fred-calendar` (FRED key) or data/manual/calendar.csv"
        )
