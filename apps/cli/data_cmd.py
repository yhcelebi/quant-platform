"""``qp data``: fetch from sources into the lake, check quality, show status."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from apps.cli.common import console, fail, settings
from qp.core import to_istanbul
from qp.core.errors import QPError
from qp.data import service
from qp.data.quality import QualityStatus
from qp.platform import Database
from qp.platform.jobs import recent_runs

app = typer.Typer(no_args_is_help=True, help="Data lake: fetch, quality, status.")

_QSTYLE = {QualityStatus.OK: "green", QualityStatus.WARN: "yellow", QualityStatus.FAIL: "red"}


class Source(StrEnum):
    TCMB = "tcmb"
    EVDS = "evds"
    FRED = "fred"
    FRED_CALENDAR = "fred-calendar"
    SEC = "sec"


def _day(text: str | None) -> date | None:
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        fail(f"expected YYYY-MM-DD, got {text!r}")


@app.command("fetch")
def fetch(
    source: Annotated[Source, typer.Argument(help="Data source")],
    since: Annotated[
        str | None, typer.Option(help="First date (default: resume after the newest stored)")
    ] = None,
    until: Annotated[str | None, typer.Option(help="Last date (default: today)")] = None,
    series: Annotated[
        list[str] | None, typer.Option(help="EVDS/FRED series code (repeatable; default: config)")
    ] = None,
) -> None:
    """Fetch new data into the lake (raw payloads kept; clean rows point-in-time).

    tcmb resumes after the newest stored bulletin; fred fetches every vintage of each series;
    evds fetches from sources.evds.start (or --since) to today; fred-calendar fetches FRED
    release dates (past 30 days and future) into the economic calendar; sec fetches XBRL
    company facts for sources.sec.tickers (or --series TICKER).
    """
    s = settings()
    try:
        with Database.from_settings(s.db) as db:
            if source is Source.TCMB:
                report = service.fetch_tcmb(s, db, since=_day(since), until=_day(until))
            elif source is Source.EVDS:
                report = service.fetch_evds(
                    s, db, since=_day(since), until=_day(until), series=tuple(series or ()) or None
                )
            elif source is Source.SEC:
                report = service.fetch_sec(s, db, tickers=tuple(series or ()) or None)
            elif source is Source.FRED_CALENDAR:
                report = service.fetch_fred_calendar(s, db)
            else:
                report = service.fetch_fred(s, db, series=tuple(series or ()) or None)
    except QPError as exc:
        fail(str(exc))
    console.print(
        f"{source}: requested {len(report.requested)} days, fetched {report.fetched}, "
        f"wrote {report.rows_written} new rows"
    )
    if report.missing:
        console.print(f"[yellow]no bulletin for:[/yellow] {', '.join(map(str, report.missing))}")
    for e in report.errors:
        console.print(f"[red]error:[/red] {escape(e)}")
    if report.errors:
        raise typer.Exit(code=1)


@app.command("quality")
def quality(
    since: Annotated[
        str | None, typer.Option(help="Check from this date (default: 90 days)")
    ] = None,
) -> None:
    """Run quality checks and store the results. Exit 1 if any check fails."""
    s = settings()
    try:
        with Database.from_settings(s.db) as db:
            results = service.run_quality(s, db, since=_day(since))
    except QPError as exc:
        fail(str(exc))
    table = Table(title="data quality")
    for col in ("dataset", "subject", "check", "status", "detail"):
        table.add_column(col, overflow="fold")
    for r in results:
        table.add_row(
            r.dataset, r.subject, r.check, f"[{_QSTYLE[r.status]}]{r.status}[/]", escape(r.detail)
        )
    console.print(table)
    if any(r.status is QualityStatus.FAIL for r in results):
        raise typer.Exit(code=1)


@app.command("status")
def status(runs: Annotated[int, typer.Option(help="Recent job runs to show")] = 10) -> None:
    """Datasets in the lake and recent job runs."""
    s = settings()
    table = Table(title=f"lake: {s.lake.root}")
    for col in ("dataset", "rows", "first", "last", "last fetched (Istanbul)"):
        table.add_column(col)
    for d in service.dataset_status(s):
        fetched = to_istanbul(d.last_fetched).strftime("%Y-%m-%d %H:%M") if d.last_fetched else "—"
        table.add_row(
            d.name, str(d.rows), str(d.first_valid or "—"), str(d.last_valid or "—"), fetched
        )
    console.print(table)
    try:
        with Database.from_settings(s.db) as db:
            rows = recent_runs(db, runs)
    except Exception as exc:  # status should still show the lake without a database
        console.print(f"[yellow]job runs unavailable:[/yellow] {type(exc).__name__}")
        return
    jobs = Table(title="recent job runs")
    for col in ("id", "job", "started (Istanbul)", "status", "detail"):
        jobs.add_column(col, overflow="fold")
    for r in rows:
        first = r.detail.splitlines()[0] if r.detail else ""
        jobs.add_row(
            str(r.id),
            r.job,
            to_istanbul(r.started_at).strftime("%Y-%m-%d %H:%M"),
            r.status,
            escape(first),
        )
    console.print(jobs)


_INFO_HINTS = ("code", "name", "freq", "start", "end")


@app.command("evds-series")
def evds_series(
    code: Annotated[str, typer.Argument(help="Series code or data group (bie_...)")],
) -> None:
    """Look up EVDS series metadata (name, frequency, first/last date) to confirm a code."""
    s = settings()
    try:
        rows = service.evds_series_info(s, code)
    except QPError as exc:
        fail(str(exc))
    if not rows:
        fail(f"EVDS returned nothing for {code}")
    every = sorted({k for r in rows for k in r})
    # Field-name casing is not documented; pick the informative ones case-insensitively.
    keys = [k for k in every if any(h in k.lower() for h in _INFO_HINTS)] or every[:8]
    table = Table(title=f"EVDS {code}")
    for k in keys:
        table.add_column(k, overflow="fold")
    for r in rows:
        table.add_row(*(escape(str(r.get(k, ""))) for k in keys))
    console.print(table)
