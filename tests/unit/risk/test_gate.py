"""Pre-trade gate (ADR 0015, 2): every rejection reason, and the flatten exception."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from qp.risk.gate import Limits, OrderRequest, check

TODAY = date(2026, 10, 26)
LIMITS = Limits()


def req(**kw: object) -> OrderRequest:
    base: dict[str, object] = {
        "strategy": "s",
        "mode": "paper",
        "bar_date": TODAY,
        "last_data_date": TODAY,
        "target": 1.0,
        "delta_quantity": Decimal(100),
        "price": Decimal(10),
        "equity": Decimal(10000),
        "previous_equity": Decimal(10000),
    }
    base.update(kw)
    return OrderRequest(**base)  # type: ignore[arg-type]


def test_clean_order_passes() -> None:
    d = check(req(), status="paper", halted=None, limits=LIMITS)
    assert (d.accepted, d.reasons, d.halt) == (True, [], None)


def test_each_rejection_reason() -> None:
    cases = {
        "kill switch": check(req(), status="paper", halted="global: x", limits=LIMITS),
        "may not trade in paper": check(req(), status="backtested", halted=None, limits=LIMITS),
        "may not trade in live": check(
            req(mode="live"), status="paper", halted=None, limits=LIMITS
        ),
        "stale data": check(
            req(last_data_date=date(2026, 10, 20)), status="paper", halted=None, limits=LIMITS
        ),
        "outside [0, 1]": check(req(target=-0.5), status="paper", halted=None, limits=LIMITS),
        "above 1.05 x equity": check(
            req(delta_quantity=Decimal(1100)), status="paper", halted=None, limits=LIMITS
        ),
        "no equity": check(req(equity=Decimal(0)), status="paper", halted=None, limits=LIMITS),
    }
    for reason, d in cases.items():
        assert not d.accepted, reason
        assert any(reason in r for r in d.reasons), (reason, d.reasons)


def test_daily_loss_halts() -> None:
    d = check(req(equity=Decimal(9400)), status="paper", halted=None, limits=LIMITS)  # -6%
    assert not d.accepted
    assert d.halt is not None
    assert "daily loss 6.00%" in d.halt
    ok = check(req(equity=Decimal(9600)), status="paper", halted=None, limits=LIMITS)  # -4%
    assert ok.accepted


def test_flatten_passes_the_kill_switch_but_nothing_else() -> None:
    flat = req(target=0.0, delta_quantity=Decimal(-100))
    assert check(flat, status="retired", halted="global: x", limits=LIMITS, flatten=True).accepted
    assert not check(flat, status="paper", halted="global: x", limits=LIMITS).accepted
    buy = req(target=1.0)
    assert not check(buy, status="paper", halted="global: x", limits=LIMITS, flatten=True).accepted
