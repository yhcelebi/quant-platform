"""A series as known at two moments, and the revisions between them (ADR 0012, 3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from qp.data.series import SeriesData


@dataclass(frozen=True, slots=True)
class Revision:
    valid_date: date
    before: Decimal | None  # None: not yet published at the earlier moment
    after: Decimal | None  # None: no longer present (rare)

    @property
    def change(self) -> Decimal | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before


def revisions(
    series: SeriesData, series_id: str, *, until: date, earlier: datetime, later: datetime
) -> list[Revision]:
    """Dates whose value differs between what was known at ``earlier`` and at ``later``,
    including values first published in between."""
    a = {p.valid_date: p.value for p in series.history(series_id, until, as_of=earlier)}
    b = {p.valid_date: p.value for p in series.history(series_id, until, as_of=later)}
    return [
        Revision(d, a.get(d), b.get(d)) for d in sorted(set(a) | set(b)) if a.get(d) != b.get(d)
    ]
