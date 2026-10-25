"""Shocks, historical replays and worst observed k-day losses on today's holdings
(ADR 0016, 6-9). TRY results are Decimal rounded to kuruş; nothing is filled in for a holding
without data: it is listed and left out of the total."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from qp.core import TRY, float_to_decimal, to_float64
from qp.data.market import MarketHistory, latest_on
from qp.risk.var import Holding, asset_returns
from qp.scenarios.definitions import Replay, Shock

ZERO = Decimal(0)
CENT = Decimal("0.01")
MAX_GAP_DAYS = 7  # a level older than this at a window's end counts as missing


@dataclass(frozen=True, slots=True)
class Row:
    name: str
    value_try: Decimal
    change: Decimal | None  # fraction of TRY value; None = no data
    pnl: Decimal | None
    note: str = ""


@dataclass
class ScenarioResult:
    name: str
    kind: str  # shock | replay | worst
    description: str = ""
    window: tuple[date, date] | None = None
    rows: list[Row] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unavailable: str | None = None

    @property
    def value_try(self) -> Decimal:
        return sum((r.value_try for r in self.rows), ZERO)

    @property
    def covered_try(self) -> Decimal:
        return sum((r.value_try for r in self.rows if r.pnl is not None), ZERO)

    @property
    def pnl(self) -> Decimal:
        return sum((r.pnl for r in self.rows if r.pnl is not None), ZERO)

    @property
    def change(self) -> Decimal | None:
        """P&L over the value of the holdings it covers."""
        return self.pnl / self.covered_try if self.covered_try else None

    @property
    def missing(self) -> list[Row]:
        return [r for r in self.rows if r.pnl is None]


def _pnl(value: Decimal, change: Decimal) -> Decimal:
    return (value * change).quantize(CENT)


def apply_shock(
    holdings: Sequence[Holding], shock: Shock, asset_class: Callable[[Holding], str]
) -> ScenarioResult:
    """(1 + price change)(1 + FX change) - 1 per holding; TRY cash is unaffected."""
    out = ScenarioResult(shock.name, "shock", shock.description)
    rate_hit: list[str] = []
    for h in holdings:
        fx = shock.fx.get(h.currency, ZERO) if h.currency != TRY else ZERO
        price = ZERO
        parts: list[str] = []
        if h.instrument is not None:
            if h.instrument in shock.instrument:
                price = shock.instrument[h.instrument]
                parts.append(f"price {price:+.1%}")
            elif h.instrument.venue in shock.venue:
                price = shock.venue[h.instrument.venue]
                parts.append(f"{h.instrument.venue} {price:+.1%}")
            elif (cls := asset_class(h)) in shock.asset_class:
                price = shock.asset_class[cls]
                parts.append(f"{cls} {price:+.1%}")
            if shock.rate_bp and h.instrument in shock.durations:
                move = -shock.durations[h.instrument] * shock.rate_bp / Decimal(10000)
                price = (1 + price) * (1 + move) - 1
                parts.append(f"rates {shock.rate_bp:+}bp x D {shock.durations[h.instrument]}")
                rate_hit.append(h.name)
        if fx:
            parts.append(f"{h.currency} {fx:+.1%}")
        change = (1 + price) * (1 + fx) - 1
        out.rows.append(
            Row(h.name, h.value_try, change, _pnl(h.value_try, change), ", ".join(parts))
        )
    if shock.rate_bp:
        out.notes.append(
            "rate shock applied to "
            + (", ".join(rate_hit) if rate_hit else "no holding")
            + " (only instruments with a duration in the scenario file)"
        )
    return out


def try_level(
    h: Holding, on: date, market: MarketHistory, *, as_of: datetime
) -> tuple[Decimal | None, date | None]:
    """TRY value of one unit (price x rate; cash: the rate) from the latest data on or
    before ``on`` that is at most ``MAX_GAP_DAYS`` old; the oldest date used is returned."""
    if h.instrument is None and h.currency == TRY:
        return Decimal(1), on
    oldest = on
    rate = Decimal(1)
    if h.currency != TRY:
        fx = latest_on(market.fx_history(h.currency, on, as_of=as_of), on)
        if fx is None or (on - fx.valid_date).days > MAX_GAP_DAYS:
            return None, None
        rate, oldest = fx.rate_try, fx.valid_date
    if h.instrument is None:
        return rate, oldest
    p = latest_on(market.price_history(h.instrument, on, as_of=as_of), on)
    if p is None or (on - p.valid_date).days > MAX_GAP_DAYS:
        return None, None
    return p.price * rate, min(oldest, p.valid_date)


def replay(
    holdings: Sequence[Holding], window: Replay, market: MarketHistory, *, as_of: datetime
) -> ScenarioResult:
    """Each holding's TRY level change over the window, applied to today's value."""
    out = ScenarioResult(window.name, "replay", window.description, (window.start, window.end))
    for h in holdings:
        start, _ = try_level(h, window.start, market, as_of=as_of)
        end, _ = try_level(h, window.end, market, as_of=as_of)
        if start is None or end is None or start <= 0:
            out.rows.append(Row(h.name, h.value_try, None, None, "no data for this window"))
            continue
        change = end / start - 1
        out.rows.append(Row(h.name, h.value_try, change, _pnl(h.value_try, change)))
    if not out.covered_try:
        out.unavailable = "no holding has data at both ends of this window"
    return out


def worst_window(
    holdings: Sequence[Holding],
    days: Sequence[date],
    market: MarketHistory,
    *,
    as_of: datetime,
    k: int,
    min_observations: int = 60,
) -> ScenarioResult:
    """The worst k-valuation-day P&L of today's holdings over ``days`` (each asset's TRY
    level from its fresh daily returns; holdings with too little history are listed)."""
    out = ScenarioResult(f"worst {k}-day", "worst")
    kept: list[tuple[Holding, list[float]]] = []
    for h in holdings:
        rets, mask = asset_returns(h, days, market, as_of=as_of)
        is_try_cash = h.instrument is None and h.currency == TRY
        if not is_try_cash and sum(mask[1:]) < min_observations:
            out.rows.append(Row(h.name, h.value_try, None, None, "too little daily history"))
            continue
        level = [1.0]
        for r in rets[1:]:
            level.append(level[-1] * (1 + r))
        kept.append((h, level))
    if not kept or len(days) <= k:
        out.unavailable = f"needs more than {k} valuation days of history"
        out.rows += [Row(h.name, h.value_try, None, None) for h, _ in kept]
        return out
    worst_t, worst = k, 0.0
    for t in range(k, len(days)):
        pnl = sum(to_float64(h.value_try) * (lv[t] / lv[t - k] - 1) for h, lv in kept)
        if pnl < worst:
            worst_t, worst = t, pnl
    out.window = (days[worst_t - k], days[worst_t])
    for h, lv in kept:
        change = float_to_decimal(lv[worst_t] / lv[worst_t - k] - 1, places=10)
        out.rows.append(Row(h.name, h.value_try, change, _pnl(h.value_try, change)))
    if worst == 0.0:
        out.notes.append("no k-day window lost money")
    out.notes.append(f"searched {days[0]} to {days[-1]}")
    return out


def history_days(day: date, years: int = 10) -> list[date]:
    """Weekdays over the last ``years`` years up to ``day``."""
    start = day - timedelta(days=365 * years + years // 4)
    return [
        start + timedelta(days=i)
        for i in range((day - start).days + 1)
        if (start + timedelta(days=i)).weekday() < 5
    ]
