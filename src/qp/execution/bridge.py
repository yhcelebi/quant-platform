"""Fill -> ledger bridge (ADR 0015, 5): broker fills as canonical ledger transactions.

Paper mode replays them in memory to reconcile; nothing reaches the owner's ledger (paper
money must not change real net worth). Live mode (phase 8) will persist them to the
strategy's trading account.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from qp.core import Currency, InstrumentId
from qp.ledger import builders as b
from qp.ledger.domain import Transaction, TxType
from qp.ledger.engine import replay

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class BrokerFill:
    day: date
    quantity: Decimal  # signed
    price: Decimal
    fee: Decimal


def _common(account: str, day: date, seq: int) -> b.Common:
    return b.Common(account, day, datetime.combine(day, time(18), tzinfo=UTC), sequence=seq)


def transactions(
    account: str,
    *,
    start: date,
    capital: Decimal,
    cash_currency: Currency,
    fills: Sequence[BrokerFill],
    instrument: InstrumentId | None = None,
    fx: Currency | None = None,
) -> list[Transaction]:
    """Opening cash plus one transaction per fill. Equities: buy/sell with the fee leg.
    FX: a conversion whose TRY side includes the fee (FX conversions carry no fee leg)."""
    txs = [b.opening_cash(_common(account, start, 0), cash_currency, capital)]
    for i, f in enumerate(fills, start=1):
        c = _common(account, f.day, i)
        notional = abs(f.quantity) * f.price
        if fx is not None:
            if f.quantity > 0:
                txs.append(b.fx_conversion(c, cash_currency, notional + f.fee, fx, f.quantity))
            else:
                txs.append(b.fx_conversion(c, fx, -f.quantity, cash_currency, notional - f.fee))
        else:
            assert instrument is not None
            side = TxType.BUY if f.quantity > 0 else TxType.SELL
            txs.append(
                b.trade(c, side, instrument, cash_currency, abs(f.quantity), f.price, fee=f.fee)
            )
    return txs


@dataclass(frozen=True, slots=True)
class LedgerView:
    quantity: Decimal
    cash: Decimal


def ledger_view(
    txs: Sequence[Transaction],
    account: str,
    *,
    cash_currency: Currency,
    instrument: InstrumentId | None = None,
    fx: Currency | None = None,
) -> LedgerView:
    state = replay(txs)
    cash = state.cash_balances().get((account, cash_currency), ZERO)
    if fx is not None:
        qty = state.cash_balances().get((account, fx), ZERO)
    else:
        assert instrument is not None
        pos = state.position(account, instrument)
        qty = pos.quantity if pos is not None else ZERO
    return LedgerView(qty, cash)
