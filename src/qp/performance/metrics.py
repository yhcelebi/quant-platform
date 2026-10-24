"""Performance over a window of a :class:`~qp.performance.history.History` (ADR 0010, 6-10).

Views: TRY (nominal), USD (values and flows / USD/TRY of the day) and real TRY (deflated by
the interpolated TÜFE level of the day, expressed in TRY of the window's last day).

Daily return: ``1 + r_d = (V_d - F_d) / V_prev`` (flows at end of day); chaining them is the
DOMAIN TWR. Returns and money stay Decimal; volatility and drawdown statistics are float64
analytics, converted at the qp.core boundary.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
from enum import StrEnum
from itertools import pairwise

from qp.core import QPValidationError, to_float64_list
from qp.data.series import SeriesPoint
from qp.performance.history import DayRecord, History
from qp.performance.returns import twr, xirr
from qp.stats import drawdowns, volatility

ZERO = Decimal(0)
ONE = Decimal(1)


class View(StrEnum):
    TRY = "try"
    USD = "usd"
    REAL = "real"


VIEW_LABEL = {View.TRY: "TRY", View.USD: "USD", View.REAL: "real TRY"}


@dataclass(frozen=True, slots=True)
class Unavailable:
    """Why a figure cannot be computed (never estimated)."""

    reason: str


class InflationIndex:
    """Daily price-index level from monthly levels (ADR 0010, 7).

    ``L_m`` (the point whose valid date is the first of month ``m``) is the level at the end
    of month ``m``; inside month ``m``: ``L_{m-1} * (L_m / L_{m-1}) ** (day / days_in_month)``.
    """

    def __init__(self, points: Sequence[SeriesPoint]) -> None:
        self.levels: dict[tuple[int, int], Decimal] = {}
        for p in points:
            if p.value <= 0:
                raise QPValidationError(f"index level must be positive: {p}")
            self.levels[(p.valid_date.year, p.valid_date.month)] = p.value

    def level(self, d: date) -> Decimal | None:
        this = self.levels.get((d.year, d.month))
        prev_key = (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
        prev = self.levels.get(prev_key)
        if this is None or prev is None:
            return None
        days = calendar.monthrange(d.year, d.month)[1]
        if d.day == days:
            return this
        with localcontext() as ctx:
            ctx.prec = 34
            return prev * (this / prev) ** (Decimal(d.day) / Decimal(days))

    @property
    def available_until(self) -> date | None:
        """Last day with a level: the end of the latest month whose predecessor is known."""
        months = sorted(m for m in self.levels if self._prev(m) in self.levels)
        if not months:
            return None
        y, m = months[-1]
        return date(y, m, calendar.monthrange(y, m)[1])

    @staticmethod
    def _prev(month: tuple[int, int]) -> tuple[int, int]:
        y, m = month
        return (y - 1, 12) if m == 1 else (y, m - 1)


@dataclass(frozen=True, slots=True)
class Point:
    day: date
    value: Decimal
    flow: Decimal


def view_points(
    records: Sequence[DayRecord],
    view: View,
    *,
    usd_try: Callable[[date], Decimal | None] | None = None,
    index: InflationIndex | None = None,
) -> list[Point] | Unavailable:
    """Convert complete TRY records into a view; any gap makes the view unavailable."""
    if not records:
        return Unavailable("no valuation days in the window")
    rows: list[tuple[date, Decimal, Decimal]] = []
    for r in records:
        if r.value is None or r.flow is None:
            what = "; ".join(r.missing[:3]) or "incomplete valuation"
            return Unavailable(f"{r.day}: {what}")
        rows.append((r.day, r.value, r.flow))
    if view is View.TRY:
        return [Point(d, v, f) for d, v, f in rows]
    if view is View.USD:
        if usd_try is None:
            return Unavailable("no USD/TRY source")
        out: list[Point] = []
        for d, v, f in rows:
            rate = usd_try(d)
            if rate is None:
                return Unavailable(f"{d}: no USD/TRY rate")
            out.append(Point(d, v / rate, f / rate))
        return out
    if index is None:
        return Unavailable("no inflation index configured (performance.inflation_series)")
    last_day = rows[-1][0]
    base = index.level(last_day)
    if base is None:
        until = index.available_until
        return Unavailable(
            f"inflation index not available for {last_day}"
            + (f"; real figures run to {until}" if until else "")
        )
    out = []
    for d, v, f in rows:
        level = index.level(d)
        if level is None:
            return Unavailable(f"{d}: inflation index not available")
        factor = base / level
        out.append(Point(d, v * factor, f * factor))
    return out


def daily_returns(points: Sequence[Point]) -> list[tuple[date, Decimal]]:
    """``(V_d - F_d) / V_prev - 1`` for each day after the first with ``V_prev > 0``."""
    out: list[tuple[date, Decimal]] = []
    for prev, cur in pairwise(points):
        if prev.value > 0:
            out.append((cur.day, (cur.value - cur.flow) / prev.value - ONE))
    return out


def wealth_index(
    returns: Sequence[tuple[date, Decimal]], start: date
) -> list[tuple[date, Decimal]]:
    """Growth of 1 from ``start``: the TWR index used for drawdowns and charts."""
    w = ONE
    out = [(start, w)]
    for d, r in returns:
        w *= ONE + r
        out.append((d, w))
    return out


def period_returns(
    returns: Sequence[tuple[date, Decimal]], *, by: str = "month"
) -> dict[tuple[int, int], Decimal]:
    """Chained return per calendar month ``(year, month)`` or year ``(year, 0)``."""
    if by not in ("month", "year"):
        raise QPValidationError("by must be 'month' or 'year'")
    growth: dict[tuple[int, int], Decimal] = {}
    for d, r in returns:
        key = (d.year, d.month if by == "month" else 0)
        growth[key] = growth.get(key, ONE) * (ONE + r)
    return {k: g - ONE for k, g in growth.items()}


def annualize(total: Decimal, days: int) -> Decimal | None:
    """(1 + R) ** (365 / d) - 1 for windows of at least 365 days; None for shorter ones."""
    if days < 365:
        return None
    if total <= -1:
        return Decimal(-1)
    with localcontext() as ctx:
        ctx.prec = 34
        return (ONE + total) ** (Decimal(365) / Decimal(days)) - ONE


@dataclass(frozen=True, slots=True)
class DrawdownInfo:
    depth: Decimal
    peak: date
    trough: date
    recovery: date | None


@dataclass
class Summary:
    view: View
    start: date
    end: date
    start_value: Decimal
    end_value: Decimal
    net_flows: Decimal
    pnl: Decimal
    twr: Decimal
    twr_annualized: Decimal | None
    xirr: float | None
    volatility: float | None
    max_drawdown: DrawdownInfo | None
    current_drawdown: Decimal
    returns: list[tuple[date, Decimal]] = field(default_factory=list)
    wealth: list[tuple[date, Decimal]] = field(default_factory=list)
    values: list[Point] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def days(self) -> int:
        return (self.end - self.start).days


def summarize(points: Sequence[Point], view: View) -> Summary | Unavailable:
    """Performance over the whole of ``points`` (the first point is the starting value; its
    flow is part of the starting value)."""
    if len(points) < 2:
        return Unavailable("need at least two valuation days")
    first, last = points[0], points[-1]
    rets = daily_returns(points)
    flows = points[1:]
    net = sum((p.flow for p in flows), ZERO)
    total = twr([r for _, r in rets]) if rets else ZERO
    notes: list[str] = []
    gaps = sum(1 for prev in points[:-1] if prev.value <= 0)
    if gaps:
        notes.append(f"{gaps} day(s) with no capital at risk were skipped in the TWR")

    cash_flows: list[tuple[date, Decimal]] = []
    if first.value != 0:
        cash_flows.append((first.day, -first.value))
    cash_flows += [(p.day, -p.flow) for p in flows if p.flow != 0]
    cash_flows.append((last.day, last.value))
    irr: float | None
    try:
        irr = xirr(cash_flows)
    except QPValidationError as exc:
        irr = None
        notes.append(f"XIRR unavailable: {exc}")

    wealth = wealth_index(rets, first.day)
    vol = volatility(to_float64_list([r for _, r in rets])) if len(rets) >= 2 else None
    wf = to_float64_list([w for _, w in wealth])
    episodes = drawdowns(wf) if all(x > 0 for x in wf) else []
    worst = episodes[0] if episodes else None
    peak_w = max((w for _, w in wealth), default=ONE)
    return Summary(
        view=view,
        start=first.day,
        end=last.day,
        start_value=first.value,
        end_value=last.value,
        net_flows=net,
        pnl=last.value - first.value - net,
        twr=total,
        twr_annualized=annualize(total, (last.day - first.day).days),
        xirr=irr,
        volatility=vol,
        max_drawdown=(
            DrawdownInfo(
                wealth[worst.trough][1] / wealth[worst.peak][1] - ONE,
                wealth[worst.peak][0],
                wealth[worst.trough][0],
                wealth[worst.recovery][0] if worst.recovery is not None else None,
            )
            if worst is not None
            else None
        ),
        current_drawdown=wealth[-1][1] / peak_w - ONE,
        returns=rets,
        wealth=wealth,
        values=list(points),
        notes=notes,
    )


def performance(
    history: History,
    view: View = View.TRY,
    *,
    start: date | None = None,
    end: date | None = None,
    usd_try: Callable[[date], Decimal | None] | None = None,
    index: InflationIndex | None = None,
) -> Summary | Unavailable:
    """Summary over [start, end] of ``history`` in ``view``."""
    records = history.window(start, end)
    points = view_points(records, view, usd_try=usd_try, index=index)
    if isinstance(points, Unavailable):
        return points
    result = summarize(points, view)
    if isinstance(result, Summary):
        result.stale = list(dict.fromkeys(s for r in records for s in r.stale))
    return result
