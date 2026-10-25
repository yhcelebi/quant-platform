"""Shocks, replays and worst windows on holdings, checked by hand (ADR 0016, 6-9)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from qp.core import TRY, USD, InstrumentId, QPValidationError
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import FxPoint, PricePoint
from qp.performance.history import valuation_days
from qp.risk.var import Holding
from qp.scenarios.definitions import Replay, Shock, load, parse
from qp.scenarios.engine import apply_shock, replay, try_level, worst_window

LATEST = datetime.max.replace(tzinfo=UTC)
THY = InstrumentId.parse("THYAO.BIST")
SPY = InstrumentId.parse("SPY.ARCX")
BOND = InstrumentId.parse("TRT1.BIST")
HOLDINGS = [
    Holding(THY, TRY, Decimal(10000)),
    Holding(SPY, USD, Decimal(20000)),
    Holding(BOND, TRY, Decimal(5000)),
    Holding(None, USD, Decimal(4000)),
    Holding(None, TRY, Decimal(1000)),
]
CLASSES = {THY: "equity", SPY: "etf", BOND: "bond"}


def _cls(h: Holding) -> str:
    return "cash" if h.instrument is None else CLASSES[h.instrument]


def test_fx_shock_hits_foreign_holdings_only() -> None:
    r = apply_shock(HOLDINGS, Shock("TRY -20%", fx={USD: Decimal("0.25")}), _cls)
    pnl = {row.name: row.pnl for row in r.rows}
    assert pnl == {
        "THYAO.BIST": Decimal("0.00"),
        "SPY.ARCX": Decimal("5000.00"),
        "TRT1.BIST": Decimal("0.00"),
        "cash USD": Decimal("1000.00"),
        "cash TRY": Decimal("0.00"),
    }
    assert r.pnl == Decimal("6000.00")
    assert r.change == Decimal("0.15")  # 6000 / 40000


def test_price_and_fx_compound_and_instrument_wins_over_venue_and_class() -> None:
    """SPY: (1 - 0.30)(1 + 0.25) - 1 = -0.125 -> -2500. THYAO: instrument -0.10 wins over
    venue BIST -0.25 and class equity -0.30 -> -1000. Bond: venue BIST -0.25 -> -1250."""
    s = Shock(
        "mix",
        fx={USD: Decimal("0.25")},
        asset_class={"equity": Decimal("-0.30"), "etf": Decimal("-0.30")},
        instrument={THY: Decimal("-0.10")},
        venue={"BIST": Decimal("-0.25")},
    )
    pnl = {row.name: row.pnl for row in apply_shock(HOLDINGS, s, _cls).rows}
    assert pnl["SPY.ARCX"] == Decimal("-2500.00")
    assert pnl["THYAO.BIST"] == Decimal("-1000.00")
    assert pnl["TRT1.BIST"] == Decimal("-1250.00")


def test_rate_shock_only_with_a_duration() -> None:
    """+500bp x D 2 = -10% on the bond only; the note lists where it applied."""
    s = Shock("rates", rate_bp=Decimal(500), durations={BOND: Decimal(2)})
    r = apply_shock(HOLDINGS, s, _cls)
    pnl = {row.name: row.pnl for row in r.rows}
    assert pnl["TRT1.BIST"] == Decimal("-500.00")
    assert pnl["THYAO.BIST"] == Decimal("0.00")
    assert "TRT1.BIST" in r.notes[0]
    none = apply_shock(HOLDINGS, Shock("rates", rate_bp=Decimal(500)), _cls)
    assert "no holding" in none.notes[0]


def _market() -> ManualMarketData:
    days = valuation_days(date(2018, 7, 2), date(2018, 9, 28))
    prices, fx = [], []
    for i, d in enumerate(days):
        k = default_known_time(d)
        rate = Decimal("4.80") if d <= date(2018, 7, 31) else Decimal("6.00")
        fx.append(FxPoint(USD, d, rate, "t", k))
        thy = Decimal(20) - Decimal(i) / 10  # falls 0.1 a day
        prices.append(PricePoint(THY, d, thy, TRY, "t", k))
    # SPY: one price only, long before the window
    prices.append(
        PricePoint(
            SPY, date(2018, 1, 2), Decimal(270), USD, "t", default_known_time(date(2018, 1, 2))
        )
    )
    return ManualMarketData(prices, fx)


def test_replay_uses_levels_at_both_ends_and_lists_missing() -> None:
    """USD 4.80 -> 6.00: +25%. THYAO Jul 31 (day 21: 17.9) -> Aug 13 (day 30: 17.0)."""
    md = _market()
    r = replay(HOLDINGS, Replay("Aug 2018", date(2018, 7, 31), date(2018, 8, 13)), md, as_of=LATEST)
    rows = {row.name: row for row in r.rows}
    assert rows["cash USD"].pnl == Decimal("1000.00")
    assert rows["THYAO.BIST"].change == Decimal("17.0") / Decimal("17.9") - 1
    assert rows["SPY.ARCX"].pnl is None  # price older than 7 days: no level
    assert rows["TRT1.BIST"].pnl is None
    assert rows["cash TRY"].pnl == Decimal("0.00")
    assert {m.name for m in r.missing} == {"SPY.ARCX", "TRT1.BIST"}
    assert r.covered_try == Decimal(15000)


def test_try_level_respects_gap_and_as_of() -> None:
    md = _market()
    level, _ = try_level(Holding(SPY, USD, Decimal(1)), date(2018, 1, 5), md, as_of=LATEST)
    assert level is None  # a SPY price, but no USD/TRY rate known on or before Jan 5
    early = datetime(2018, 7, 1, tzinfo=UTC)
    level, _ = try_level(Holding(None, USD, Decimal(1)), date(2018, 8, 1), md, as_of=early)
    assert level is None  # nothing was known yet


def test_worst_window_finds_the_jump() -> None:
    """USD cash 4000: the 1-day jump Jul 31 -> Aug 1 is +25% (a gain); THYAO falls every
    day, so with THYAO alone the worst 5-day window is the one with the largest relative
    fall: the last five days (lowest prices)."""
    md = _market()
    days = valuation_days(date(2018, 7, 2), date(2018, 9, 28))
    only_thy = [Holding(THY, TRY, Decimal(10000))]
    r = worst_window(only_thy, days, md, as_of=LATEST, k=5, min_observations=20)
    assert r.window == (days[-6], days[-1])
    lv_start = Decimal(20) - Decimal(len(days) - 6) / 10
    lv_end = Decimal(20) - Decimal(len(days) - 1) / 10
    assert r.pnl == pytest.approx(Decimal(10000) * (lv_end / lv_start - 1), abs=Decimal("0.01"))
    with_usd = worst_window(
        [*only_thy, Holding(None, USD, Decimal(4000))],
        days,
        md,
        as_of=LATEST,
        k=1,
        min_observations=20,
    )
    assert with_usd.window is not None
    assert with_usd.window[1] != date(2018, 8, 1)  # the jump day was a gain
    sparse = worst_window(
        [Holding(SPY, USD, Decimal(1))], days, md, as_of=LATEST, k=1, min_observations=20
    )
    assert sparse.unavailable is not None


def test_parse_and_validate(tmp_path: Path) -> None:
    data = {
        "shocks": [{"name": "a", "fx": {"usd": "0.1"}, "venue": {"bist": -0.2}}],
        "replays": [{"name": "b", "start": date(2020, 1, 1), "end": date(2020, 2, 1)}],
    }
    s = parse(data)
    assert s.shocks[0].fx == {USD: Decimal("0.1")}
    assert s.shocks[0].venue == {"BIST": Decimal("-0.2")}
    assert s.replays[0].end == date(2020, 2, 1)
    with pytest.raises(QPValidationError):
        parse({"shocks": [{"name": "x", "fx": {"EUR": -1}}]})
    with pytest.raises(QPValidationError):
        parse({"replays": [{"name": "x", "start": date(2020, 2, 1), "end": date(2020, 1, 1)}]})
    with pytest.raises(QPValidationError):
        parse(
            {
                "shocks": [{"name": "x"}],
                "replays": [{"name": "x", "start": date(2020, 1, 1), "end": date(2020, 1, 2)}],
            }
        )
    assert load(tmp_path / "missing.yaml").shocks == []


def test_shipped_scenarios_file_parses() -> None:
    s = load(Path("configs/scenarios.yaml"))
    assert len(s.shocks) >= 4
    assert len(s.replays) >= 4
    raw = yaml.safe_load(Path("configs/scenarios.yaml").read_text())
    assert {x["name"] for x in raw["shocks"]} == {x.name for x in s.shocks}
