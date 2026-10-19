"""Data quality checks over clean rows. Pure: they report, they never fix (ADR 0007).

- missing_sessions: calendar days with no value
- stale: the newest value is older than the last day that should have one
- not_positive: zero or negative values
- jumps: day-to-day moves beyond a threshold (prices also need a corporate action to explain)
- known_before_valid: a value known before the day it describes began (look-ahead suspect)
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Any

from qp.core import assume_istanbul, ensure_utc


class QualityStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    dataset: str
    check: str
    subject: str
    status: QualityStatus
    detail: str = ""


def _fmt_days(days: Sequence[date], limit: int = 8) -> str:
    shown = ", ".join(str(d) for d in days[:limit])
    return shown + (f" (+{len(days) - limit} more)" if len(days) > limit else "")


def missing_sessions(
    dataset: str, subject: str, rows: Sequence[dict[str, Any]], expected: Sequence[date]
) -> CheckResult:
    have = {r["valid_date"] for r in rows}
    missing = [d for d in expected if d not in have]
    if not missing:
        return CheckResult(dataset, "missing_sessions", subject, QualityStatus.OK)
    return CheckResult(
        dataset,
        "missing_sessions",
        subject,
        QualityStatus.WARN,
        f"{len(missing)} of {len(expected)} expected days have no value: {_fmt_days(missing)}",
    )


def stale(
    dataset: str, subject: str, rows: Sequence[dict[str, Any]], last_expected: date
) -> CheckResult:
    if not rows:
        return CheckResult(dataset, "stale", subject, QualityStatus.FAIL, "no values at all")
    newest = max(r["valid_date"] for r in rows)
    if newest >= last_expected:
        return CheckResult(dataset, "stale", subject, QualityStatus.OK, f"latest {newest}")
    return CheckResult(
        dataset,
        "stale",
        subject,
        QualityStatus.WARN,
        f"latest value is for {newest}; expected one for {last_expected}",
    )


def not_positive(
    dataset: str, subject: str, rows: Sequence[dict[str, Any]], value: str = "value"
) -> CheckResult:
    bad = [r["valid_date"] for r in rows if r[value] is None or Decimal(r[value]) <= 0]
    if not bad:
        return CheckResult(dataset, "not_positive", subject, QualityStatus.OK)
    return CheckResult(
        dataset, "not_positive", subject, QualityStatus.FAIL, f"values <= 0 on {_fmt_days(bad)}"
    )


def jumps(
    dataset: str,
    subject: str,
    rows: Sequence[dict[str, Any]],
    *,
    threshold: Decimal,
    value: Callable[[dict[str, Any]], Decimal],
) -> CheckResult:
    """Moves between consecutive values larger than ``threshold`` (a fraction)."""
    ordered = sorted(rows, key=lambda r: r["valid_date"])
    hits = []
    for a, b in pairwise(ordered):
        va, vb = value(a), value(b)
        if va > 0 and abs(vb / va - 1) > threshold:
            hits.append(f"{a['valid_date']}->{b['valid_date']} {vb / va - 1:+.2%}")
    if not hits:
        return CheckResult(dataset, "jumps", subject, QualityStatus.OK)
    return CheckResult(
        dataset,
        "jumps",
        subject,
        QualityStatus.WARN,
        f"{len(hits)} moves beyond {threshold:.0%}: {'; '.join(hits[:5])}",
    )


def known_before_valid(dataset: str, subject: str, rows: Sequence[dict[str, Any]]) -> CheckResult:
    """A value for day D known before 00:00 Istanbul on D describes the future: suspicious."""
    bad = [
        r["valid_date"]
        for r in rows
        if ensure_utc(r["known_time"])
        < ensure_utc(assume_istanbul(datetime.combine(r["valid_date"], time())))
    ]
    if not bad:
        return CheckResult(dataset, "known_before_valid", subject, QualityStatus.OK)
    return CheckResult(
        dataset,
        "known_before_valid",
        subject,
        QualityStatus.FAIL,
        f"known before the day began on {_fmt_days(bad)}",
    )
