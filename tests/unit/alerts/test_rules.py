"""Alert rules on hand-built histories (ADR 0010, 17)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from qp.alerts.rules import Thresholds, data_alerts, performance_alerts
from qp.core import TRY, InstrumentId
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import PricePoint
from qp.ledger import builders as b
from qp.performance.history import valuation_days
from qp.performance.metrics import Point, Summary, View, summarize
from qp.portfolio.holdings import build_report

T = Thresholds(Decimal("0.03"), Decimal("0.10"), 0.99, 7)
DAYS = valuation_days(date(2025, 1, 6), date(2025, 6, 30))


def summary_of(returns: list[Decimal]) -> Summary:
    value = Decimal(1000)
    points = [Point(DAYS[0], value, value)]
    for d, r in zip(DAYS[1:], returns, strict=False):
        value *= 1 + r
        points.append(Point(d, value, Decimal(0)))
    s = summarize(points, View.TRY)
    assert isinstance(s, Summary)
    return s


def test_daily_loss_and_var_breach() -> None:
    """70 days of +-0.5%, then -5%: below the -3% limit, and beyond the 99% VaR of the
    earlier days (0.5%)."""
    rets = [Decimal("0.005") if i % 2 else Decimal("-0.005") for i in range(70)]
    s = summary_of([*rets, Decimal("-0.05")])
    alerts = {a.rule: a for a in performance_alerts(s, T)}
    assert set(alerts) == {"daily_loss", "var_breach"}
    assert alerts["daily_loss"].key_date == DAYS[71]
    assert alerts["daily_loss"].message.startswith(f"Portfolio -5.00% on {DAYS[71]}")
    assert alerts["var_breach"].level == "critical"
    calm = summary_of([*rets, Decimal("-0.004")])
    assert performance_alerts(calm, T) == []


def test_drawdown_is_keyed_by_its_peak() -> None:
    """+10%, then -6% twice: drawdown 1 - 0.94^2 = 11.64% from the peak on day 1."""
    s = summary_of([Decimal("0.1"), Decimal("-0.06"), Decimal("-0.06")])
    [dd] = [a for a in performance_alerts(s, T) if a.rule == "drawdown"]
    assert dd.key_date == DAYS[1]
    assert "Drawdown -11.64%" in dd.message


def test_missing_and_stale_prices() -> None:
    thy, aapl = InstrumentId.parse("THYAO.BIST"), InstrumentId.parse("XYZ.BIST")
    c = b.Common("A", date(2025, 1, 2), datetime(2025, 1, 2, 20, tzinfo=UTC))
    txs = [
        b.opening_position(c, thy, TRY, Decimal(1), Decimal(1)),
        b.opening_position(c, aapl, TRY, Decimal(1), Decimal(1)),
    ]
    day = date(2025, 1, 2)
    md = ManualMarketData([PricePoint(thy, day, Decimal(5), TRY, "t", default_known_time(day))], [])
    report = build_report(txs, md, as_of=day + timedelta(days=10))
    alerts = {a.rule: a for a in data_alerts(report, T)}
    assert alerts["stale_price"].subject == "THYAO.BIST"
    assert alerts["stale_price"].key_date == day
    assert alerts["missing_price"].subject == "XYZ.BIST"
    assert data_alerts(build_report(txs[:1], md, as_of=day), T) == []
