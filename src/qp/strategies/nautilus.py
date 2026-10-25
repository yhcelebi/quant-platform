"""NautilusTrader wrappers around the pure signals (ADR 0014, 3-5).

``QPStrategy`` keeps the closes it has seen, asks the registered signal for a target at each
bar, and trades that target on the *next* bar (``lag_bars``). Orders go through Nautilus
only; strategies never reach a broker adapter (``qp.execution`` is not importable here).
``QPFeeModel`` charges the dated qp cost model on every fill, identically to the reference.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal

from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.orders import Order
from nautilus_trader.trading.strategy import Strategy

from qp.strategies.costs import CostModel
from qp.strategies.signals import get

ZERO = Decimal(0)


def ns_date(ts: int) -> datetime:
    return datetime.fromtimestamp(ts / 1e9, tz=UTC)


class QPFeeModel(FeeModel):
    """qp's dated cost model as a Nautilus fee model (fee in the instrument's currency)."""

    def __init__(self, costs: CostModel) -> None:
        super().__init__()
        self.costs = costs

    def get_commission(
        self, order: Order, fill_qty: Quantity, fill_px: Price, instrument: Instrument
    ) -> Money:
        notional = Decimal(str(fill_qty)) * Decimal(str(fill_px))
        fee = self.costs.fee(notional, ns_date(order.ts_init).date())
        return Money(str(fee), instrument.quote_currency)


class QPStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    signal: str
    params_json: str = "{}"
    lag_bars: int = 1
    cost_rate: str = "0"  # proportional cost, grossed into the sizing price


def target_quantity(target: float, equity: Decimal, price: Decimal, cost_rate: Decimal) -> Decimal:
    """Whole units for a target fraction of equity, truncated toward zero (ADR 0014, 4)."""
    if price <= 0:
        return ZERO
    raw = Decimal(str(target)) * equity / (price * (1 + cost_rate))
    return raw.to_integral_value(rounding=ROUND_DOWN)


class QPStrategy(Strategy):
    def __init__(self, config: QPStrategyConfig) -> None:
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)
        self.signal = get(config.signal).fn
        self.params = json.loads(config.params_json)
        self.lag = config.lag_bars
        self.cost_rate = Decimal(config.cost_rate)
        self.closes: list[float] = []
        self.pending: list[float] = []  # targets waiting for their execution bar
        self.targets: list[float] = []

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)

    def on_bar(self, bar: Bar) -> None:
        price = Decimal(str(bar.close))
        if len(self.pending) >= self.lag:
            self._trade_to(self.pending.pop(0), price)
        self.closes.append(float(price))
        target = self.signal(self.closes, **self.params)[-1]
        self.targets.append(target)
        self.pending.append(target)

    def _trade_to(self, target: float, price: Decimal) -> None:
        venue = Venue(self.instrument_id.venue.value)
        account = self.portfolio.account(venue)
        if account is None:
            return
        instrument = self.cache.instrument(self.instrument_id)
        cash = Decimal(str(account.balance_total(instrument.quote_currency).as_decimal()))
        held = Decimal(str(self.portfolio.net_position(self.instrument_id)))
        equity = cash + held * price
        want = target_quantity(target, equity, price, self.cost_rate)
        delta = want - held
        if delta == 0:
            return
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        order = self.order_factory.market(
            self.instrument_id, side, Quantity.from_str(str(abs(delta)))
        )
        self.submit_order(order)

    def on_order_filled(self, event: OrderFilled) -> None:  # kept for subclasses / logs
        pass
