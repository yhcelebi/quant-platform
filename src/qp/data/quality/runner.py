"""Run the checks for a dataset and store the results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from qp.core import ensure_utc, istanbul_date
from qp.data.connectors import tcmb
from qp.data.lake import CleanStore
from qp.data.quality.checks import (
    CheckResult,
    jumps,
    known_before_valid,
    missing_sessions,
    not_positive,
    stale,
)
from qp.data.quality.models import DataQualityResultRow
from qp.platform.db import Database
from qp.reference.calendars import is_session, previous_session, sessions

FX_JUMP = Decimal("0.10")  # a 10% day move in a TCMB rate is worth a look


def last_tcmb_bulletin_due(now: datetime) -> date:
    """The most recent TCMB business day whose 15:30 bulletin should exist at ``now``."""
    today = istanbul_date(now)
    if is_session("TCMB", today) and tcmb.scheduled_known_time(today) <= ensure_utc(now):
        return today
    return previous_session("TCMB", today)


def check_tcmb_fx(
    clean: CleanStore,
    *,
    start: date,
    now: datetime,
    currencies: Sequence[str] = ("USD", "EUR"),
    rate_kind: str = "forex_buying",
) -> list[CheckResult]:
    """Checks over [start, last bulletin due] for the currencies qp values with."""
    end = last_tcmb_bulletin_due(now)
    rows = clean.query(
        tcmb.DATASET,
        as_of=now,
        where={"rate_kind": rate_kind},
        valid_from=start,
        valid_to=end,
    )
    by_currency: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_currency[r["currency"]].append(r)
    expected = sessions("TCMB", start, end)
    out: list[CheckResult] = []
    name = tcmb.DATASET.name
    for cur in currencies:
        series = by_currency.get(cur, [])
        subject = f"{cur} {rate_kind}"
        out.append(missing_sessions(name, subject, series, expected))
        out.append(stale(name, subject, series, end))
        out.append(not_positive(name, subject, series))
        out.append(
            jumps(
                name,
                subject,
                series,
                threshold=FX_JUMP,
                value=lambda r: Decimal(r["value"]) / Decimal(r["unit"]),
            )
        )
        out.append(known_before_valid(name, subject, series))
    return out


def save_results(db: Database, results: Sequence[CheckResult], job_run_id: int | None) -> None:
    with db.session() as s:
        s.add_all(
            DataQualityResultRow(
                job_run_id=job_run_id,
                dataset=r.dataset,
                check_name=r.check,
                subject=r.subject,
                status=r.status.value,
                detail=r.detail,
            )
            for r in results
        )


__all__ = ["FX_JUMP", "check_tcmb_fx", "last_tcmb_bulletin_due", "save_results"]
