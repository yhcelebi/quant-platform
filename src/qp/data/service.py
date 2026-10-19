"""Wiring for apps and workers: lake and HTTP clients from settings, fetch and check runs
recorded in ``ops.job_runs``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from qp.core import Clock, QPError, SystemClock, istanbul_date
from qp.data.connectors import evds, fred, tcmb
from qp.data.connectors.http import HttpClient
from qp.data.connectors.tcmb import FetchReport
from qp.data.lake import CleanStore, Dataset, RawStore
from qp.data.lake_market import plain
from qp.data.quality import CheckResult, QualityStatus, check_tcmb_fx, save_results
from qp.data.quality.models import DataQualityResultRow
from qp.platform.db import Database
from qp.platform.jobs import JobRun, job_run
from qp.platform.lake import LakePaths
from qp.platform.settings import Settings

DATASETS: tuple[Dataset, ...] = (tcmb.DATASET, evds.DATASET, fred.DATASET)
DEFAULT_LOOKBACK = timedelta(days=30)


def lake(settings: Settings) -> tuple[RawStore, CleanStore]:
    paths = LakePaths(settings.lake.root)
    return RawStore(paths), CleanStore(paths)


def http_client(settings: Settings, min_interval_s: float) -> HttpClient:
    return HttpClient(user_agent=settings.sources.user_agent, min_interval_s=min_interval_s)


def _resume_from(clean: CleanStore, ds: Dataset, today: date) -> date:
    stats = clean.stats(ds)
    if stats is None or stats["last_valid"] is None:
        return today - DEFAULT_LOOKBACK
    last: date = stats["last_valid"]
    return last + timedelta(days=1)


def fetch_tcmb(
    settings: Settings,
    db: Database,
    *,
    since: date | None = None,
    until: date | None = None,
    clock: Clock | None = None,
) -> FetchReport:
    """Fetch TCMB bulletins (default: from the day after the newest stored one, or the last
    30 days for an empty lake, up to today) and record the run."""
    clock = clock or SystemClock()
    raw, clean = lake(settings)
    today = istanbul_date(clock.now())
    start = since or _resume_from(clean, tcmb.DATASET, today)
    end = until or today
    with (
        job_run(db, "fetch:tcmb", secrets=settings.secret_values()) as run,
        http_client(settings, settings.sources.tcmb.min_interval_s) as client,
    ):
        report = tcmb.fetch_range(client, raw, clean, start, end, now=clock.now)
        _record(run, report, f"{start}..{end}: requested {len(report.requested)}, ")
    return report


class MissingKeyError(QPError):
    pass


def _record(run: JobRun, report: FetchReport, prefix: str = "") -> None:
    run.note(
        f"{prefix}fetched {report.fetched}, rows {report.rows_written}, "
        f"missing {len(report.missing)}, errors {len(report.errors)}"
    )
    for d in report.missing:
        run.note(f"missing: {d}")
    for e in report.errors:
        run.note(f"error: {e}")
    if report.errors:
        run.status = "partial" if report.rows_written else "failed"


def _key(settings: Settings, source: str) -> str:
    secret = getattr(settings.sources, source).api_key
    if secret is None or not secret.get_secret_value():
        raise MissingKeyError(
            f"no {source.upper()} API key: set QP_SOURCES__{source.upper()}__API_KEY in .env"
        )
    value: str = secret.get_secret_value()
    return value


def fetch_fred(
    settings: Settings,
    db: Database,
    *,
    series: tuple[str, ...] | None = None,
    clock: Clock | None = None,
) -> FetchReport:
    """Fetch every vintage of the configured FRED series (ALFRED)."""
    clock = clock or SystemClock()
    key = _key(settings, "fred")
    raw, clean = lake(settings)
    codes = series or settings.sources.fred.series
    with (
        job_run(db, "fetch:fred", secrets=settings.secret_values()) as run,
        http_client(settings, settings.sources.fred.min_interval_s) as client,
    ):
        report = fred.fetch_series(client, raw, clean, codes, api_key=key, now=clock.now)
        _record(run, report, f"{', '.join(codes)}: ")
    return report


def fetch_evds(
    settings: Settings,
    db: Database,
    *,
    since: date | None = None,
    until: date | None = None,
    series: tuple[str, ...] | None = None,
    clock: Clock | None = None,
) -> FetchReport:
    """Fetch the configured EVDS series, one calendar year per request."""
    clock = clock or SystemClock()
    key = _key(settings, "evds")
    codes = series or settings.sources.evds.series
    if not codes:
        raise QPError("no EVDS series configured (sources.evds.series in configs/base.yaml)")
    raw, clean = lake(settings)
    start = since or settings.sources.evds.start
    end = until or istanbul_date(clock.now())
    with (
        job_run(db, "fetch:evds", secrets=settings.secret_values()) as run,
        http_client(settings, settings.sources.evds.min_interval_s) as client,
    ):
        report = evds.fetch_series(
            client,
            raw,
            clean,
            codes,
            start,
            end,
            base_url=settings.sources.evds.base_url,
            api_key=key,
            now=clock.now,
        )
        _record(run, report, f"{', '.join(codes)} {start}..{end}: ")
    return report


def evds_series_info(settings: Settings, code: str) -> list[dict[str, object]]:
    key = _key(settings, "evds")
    with http_client(settings, settings.sources.evds.min_interval_s) as client:
        return evds.series_info(
            client, base_url=settings.sources.evds.base_url, api_key=key, code=code
        )


def run_quality(
    settings: Settings,
    db: Database,
    *,
    since: date | None = None,
    clock: Clock | None = None,
) -> list[CheckResult]:
    clock = clock or SystemClock()
    _, clean = lake(settings)
    now = clock.now()
    start = since or istanbul_date(now) - timedelta(days=90)
    with job_run(db, "quality:tcmb_fx") as run:
        results = check_tcmb_fx(
            clean, start=start, now=now, rate_kind=settings.sources.tcmb.rate_kind
        )
        save_results(db, results, run.id)
        bad = [r for r in results if r.status is not QualityStatus.OK]
        run.note(f"{len(results)} checks from {start}, {len(bad)} not ok")
        if any(r.status is QualityStatus.FAIL for r in results):
            run.status = "partial"
    return results


@dataclass(frozen=True)
class DatasetStatus:
    name: str
    rows: int
    first_valid: date | None
    last_valid: date | None
    last_fetched: datetime | None


def dataset_status(settings: Settings) -> list[DatasetStatus]:
    _, clean = lake(settings)
    out = []
    for ds in DATASETS:
        stats: dict[str, Any] | None = clean.stats(ds)
        if stats is None:
            out.append(DatasetStatus(ds.name, 0, None, None, None))
        else:
            out.append(
                DatasetStatus(
                    ds.name,
                    int(stats["rows"]),
                    stats["first_valid"],
                    stats["last_valid"],
                    stats["last_fetched"],
                )
            )
    return out


def latest_quality(db: Database) -> list[DataQualityResultRow]:
    """Results of the most recent quality run."""
    with db.session() as s:
        last = s.scalar(select(func.max(DataQualityResultRow.job_run_id)))
        if last is None:
            return []
        rows = list(
            s.scalars(
                select(DataQualityResultRow)
                .where(DataQualityResultRow.job_run_id == last)
                .order_by(DataQualityResultRow.id)
            )
        )
        s.expunge_all()
        return rows


def fx_history(
    settings: Settings, currencies: tuple[str, ...], start: date, *, as_of: datetime
) -> dict[str, list[tuple[date, Decimal]]]:
    """TCMB rate (configured kind, per 1 unit) per currency since ``start``, known at as_of."""
    _, clean = lake(settings)
    rows = clean.query(
        tcmb.DATASET,
        as_of=as_of,
        where={"rate_kind": settings.sources.tcmb.rate_kind},
        valid_from=start,
    )
    out: dict[str, list[tuple[date, Decimal]]] = {c: [] for c in currencies}
    for r in rows:
        if r["currency"] in out:
            out[r["currency"]].append((r["valid_date"], plain(r["value"] / Decimal(r["unit"]))))
    return out
