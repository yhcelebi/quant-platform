"""Report windows: today, this week (Monday to Sunday), month, quarter, year."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from qp.core import QPValidationError

PERIODS = ("day", "week", "month", "quarter", "year")


def window(period: str, day: date) -> tuple[date, date]:
    if period == "day":
        return day, day
    if period == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])
    if period == "quarter":
        m = 3 * ((day.month - 1) // 3) + 1
        end_m = m + 2
        return date(day.year, m, 1), date(day.year, end_m, calendar.monthrange(day.year, end_m)[1])
    if period == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    raise QPValidationError(f"period must be one of {', '.join(PERIODS)}")


def month(text: str) -> tuple[date, date]:
    """'2026-10' -> 1 Oct .. 31 Oct."""
    try:
        y, m = (int(x) for x in text.split("-"))
        return window("month", date(y, m, 1))
    except ValueError:
        raise QPValidationError(f"month must be YYYY-MM, got {text!r}") from None
