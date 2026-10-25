"""Run a signal through NautilusTrader (ADR 0014): one instrument, close-only bars, a cash
account in the instrument's currency, qp costs as the fee model, one-bar execution lag."""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency as NtCurrency
from nautilus_trader.model.objects import Money, Price, Quantity

from qp.backtest.data import PriceSeries
from qp.backtest.reference import Fill, SimResult, cost_buffer
from qp.core import QPValidationError, ensure_utc
from qp.strategies.costs import CostModel
from qp.strategies.nautilus import QPFeeModel, QPStrategy, QPStrategyConfig

ZERO = Decimal(0)


class _RecordingStrategy(QPStrategy):
    """QPStrategy that also records fills and rejects long-only violations, for reports."""

    def __init__(self, config: QPStrategyConfig) -> None:
        super().__init__(config)
        self.fills: list[OrderFilled] = []

    def _trade_to(self, target: float, price: Decimal) -> None:
        if not 0 <= target <= 1:
            raise QPValidationError(f"target {target}: cash accounts are long-only")
        super()._trade_to(target, price)

    def on_order_filled(self, event: OrderFilled) -> None:
        self.fills.append(event)


def _ns(t: datetime) -> int:
    return int(ensure_utc(t).timestamp() * 1_000_000_000)


def _instrument(series: PriceSeries) -> Equity:
    iid = InstrumentId.from_str(str(series.instrument))
    precision = series.price_precision
    return Equity(
        iid,
        Symbol(series.instrument.symbol),
        NtCurrency.from_str(series.currency.code),
        precision,
        Price(Decimal(1).scaleb(-precision), precision),
        Quantity.from_int(1),
        0,
        0,
    )


def bars(series: PriceSeries, bar_type: BarType, precision: int) -> list[Bar]:
    out = []
    for close, known in zip(series.closes, series.known_times, strict=True):
        p = Price(close, precision)
        ts = _ns(known)
        out.append(Bar(bar_type, p, p, p, p, Quantity.from_int(1_000_000_000), ts, ts))
    return out


def run_nautilus(
    series: PriceSeries,
    *,
    signal: str,
    params: dict[str, Any],
    capital: Decimal,
    costs: CostModel,
    lag: int = 1,
    band: Decimal = Decimal("0.01"),
) -> SimResult:
    if len(series.days) < 2:
        raise QPValidationError("a backtest needs at least two bars")
    instrument = _instrument(series)
    venue = Venue(series.instrument.venue)
    currency = NtCurrency.from_str(series.currency.code)
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId("QP-BACKTEST"), logging=LoggingConfig(bypass_logging=True)
        )
    )
    try:
        engine.add_venue(
            venue,
            OmsType.NETTING,
            AccountType.CASH,
            [Money(str(capital), currency)],
            base_currency=currency,
            fee_model=QPFeeModel(costs),
        )
        engine.add_instrument(instrument)
        bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")
        engine.add_data(bars(series, bar_type, instrument.price_precision))
        strategy = _RecordingStrategy(
            QPStrategyConfig(
                instrument_id=str(instrument.id),
                bar_type=str(bar_type),
                signal=signal,
                params_json=json.dumps(params),
                lag_bars=lag,
                cost_rate=str(cost_buffer(costs)),
                band=str(band),
            )
        )
        engine.add_strategy(strategy)
        with warnings.catch_warnings():
            # nautilus_trader 1.231 calls pandas' deprecated Timestamp.utcnow internally
            warnings.filterwarnings("ignore", message=".*Timestamp.utcnow is deprecated.*")
            engine.run()
        account = engine.portfolio.account(venue)
        cash_end = Decimal(str(account.balance_total(currency).as_decimal()))
        fills = [
            Fill(
                datetime.fromtimestamp(f.ts_event / 1e9, tz=UTC).date(),
                Decimal(str(f.last_qty)) * (1 if f.order_side == OrderSide.BUY else -1),
                Decimal(str(f.last_px)),
                Decimal(str(f.commission.as_decimal())),
            )
            for f in strategy.fills
        ]
    finally:
        engine.dispose()
    return _equity_from_fills(series, fills, capital, cash_end, strategy.targets)


def _equity_from_fills(
    series: PriceSeries,
    fills: list[Fill],
    capital: Decimal,
    cash_end: Decimal,
    targets: list[float],
) -> SimResult:
    """Daily equity from Nautilus' fills (fills are at bar closes, so on their bar's day)."""
    by_day: dict[object, list[Fill]] = {}
    for f in fills:
        by_day.setdefault(f.day, []).append(f)
    cash, qty = capital, ZERO
    out = SimResult([], [], [], cash_end, fills, targets, engine="nautilus")
    for day, price in zip(series.days, series.closes, strict=True):
        for f in by_day.get(day, []):
            cash -= f.quantity * f.price + f.fee
            qty += f.quantity
        out.days.append(day)
        out.equity.append(cash + qty * price)
        out.position.append(qty)
    return out
