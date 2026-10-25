"""Pre-trade gate and kill switch (ADR 0015, 2-3). Every order passes ``check`` first.

The gate reads promotion status and kill-switch state from ``trading`` tables through this
module (qp.risk sits below qp.strategies and qp.execution, rule 9 and ARCHITECTURE).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from qp.platform.db import Database
from qp.risk.models import PromotionRow, StrategyRow, TradingStateRow

GLOBAL = "global"
# Statuses whose strategies may trade in each mode. Live needs the owner's approval record.
ALLOWED = {"paper": frozenset({"paper", "approved_live"}), "live": frozenset({"approved_live"})}


@dataclass(frozen=True, slots=True)
class Limits:
    max_stale_days: int = 5
    max_order_fraction: Decimal = Decimal("1.05")  # of equity
    max_daily_loss: Decimal = Decimal("0.05")  # equity drop in one bar that halts


@dataclass
class Decision:
    accepted: bool = True
    reasons: list[str] = field(default_factory=list)
    halt: str | None = None  # set when the strategy must be halted

    def reject(self, reason: str) -> None:
        self.accepted = False
        self.reasons.append(reason)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    strategy: str
    mode: str  # paper | live
    bar_date: date  # the day the order is decided (today)
    last_data_date: date  # the newest close available
    target: float
    delta_quantity: Decimal
    price: Decimal
    equity: Decimal
    previous_equity: Decimal | None


def check(
    req: OrderRequest,
    *,
    status: str | None,
    halted: str | None,
    limits: Limits,
    flatten: bool = False,
) -> Decision:
    """Pure pre-trade checks (no I/O): the caller supplies status and kill-switch state.
    A flatten order (target 0, explicitly requested) passes the kill switch and status
    checks: getting out must always be possible."""
    d = Decision()
    closing = flatten and req.target == 0
    if halted is not None and not closing:
        d.reject(f"kill switch: {halted}")
    if status not in ALLOWED.get(req.mode, frozenset()) and not closing:
        d.reject(f"strategy status {status or 'none'} may not trade in {req.mode} mode")
    if (req.bar_date - req.last_data_date).days > limits.max_stale_days:
        d.reject(f"stale data: last close {req.last_data_date}")
    if not 0 <= req.target <= 1:
        d.reject(f"target {req.target} outside [0, 1]")
    notional = abs(req.delta_quantity) * req.price
    if req.equity <= 0:
        d.reject("no equity")
    elif notional > limits.max_order_fraction * req.equity:
        d.reject(f"order notional {notional:.2f} above {limits.max_order_fraction} x equity")
    if req.previous_equity and req.previous_equity > 0:
        drop = 1 - req.equity / req.previous_equity
        if drop > limits.max_daily_loss:
            d.halt = f"daily loss {drop:.2%} above {limits.max_daily_loss:.0%}"
            d.reject(d.halt)
    return d


# --------------------------------------------------------------------------- state access
def status(db: Database, strategy: str) -> str | None:
    """Latest promotion status of a strategy (None: not deployed)."""
    with db.session() as s:
        sid = s.scalar(select(StrategyRow.id).where(StrategyRow.name == strategy))
        if sid is None:
            return None
        row = s.scalars(
            select(PromotionRow)
            .where(PromotionRow.strategy_id == sid)
            .order_by(PromotionRow.id.desc())
        ).first()
        return row.status if row else None


def halted(db: Database, strategy: str) -> str | None:
    """Why trading is halted for ``strategy`` (global switch first), else None."""
    with db.session() as s:
        for scope in (GLOBAL, strategy):
            row = s.scalars(
                select(TradingStateRow)
                .where(TradingStateRow.scope == scope)
                .order_by(TradingStateRow.id.desc())
            ).first()
            if row is not None and row.halted:
                return f"{scope}: {row.reason}" if scope == GLOBAL else row.reason
    return None


def set_halt(db: Database, scope: str, *, halted_: bool, reason: str) -> None:
    """Record a kill-switch event (append-only; the latest event per scope wins)."""
    with db.session() as s:
        s.add(TradingStateRow(scope=scope, halted=halted_, reason=reason))


def flatten_requested(db: Database, strategy: str) -> bool:
    with db.session() as s:
        row = s.scalars(
            select(TradingStateRow)
            .where(TradingStateRow.scope == f"flatten:{strategy}")
            .order_by(TradingStateRow.id.desc())
        ).first()
        return bool(row and row.halted)
