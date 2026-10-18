"""Owner-owned reference cases (tests/reference/ledger_cases.yaml) run through the real code.

Read-only: this module never writes to tests/reference/. Every `expect` key must be known
to the harness; an unknown key fails the test so no expectation can be silently skipped.
Dates are not part of cases a-d; the harness assigns one calendar day per step.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

from qp.core import Currency, InstrumentId
from qp.ledger import builders as b
from qp.ledger.domain import Transaction, TxType
from qp.ledger.engine import LedgerState, replay
from qp.performance.returns import real_return, sub_period_return, twr, xirr
from qp.portfolio.valuation import value_position
from qp.reference.domain import CorporateAction, CorporateActionType, price_adjustment_factor

pytestmark = pytest.mark.reference

REFERENCE = Path(__file__).resolve().parents[1] / "reference" / "ledger_cases.yaml"
CASES: dict[str, Any] = yaml.safe_load(REFERENCE.read_text(encoding="utf-8"))["cases"]
ACCOUNT = "REF"
START = date(2025, 1, 1)


def dec(value: Any) -> Decimal:
    assert isinstance(value, str), f"reference values must be strings, got {value!r}"
    return Decimal(value)


class Runner:
    """Executes reference steps on an in-memory ledger."""

    def __init__(self) -> None:
        self.txs: list[Transaction] = []
        self.day = 0

    def common(self) -> b.Common:
        self.day += 1
        d = START + timedelta(days=self.day)
        recorded = datetime(d.year, d.month, d.day, 18, tzinfo=UTC)
        return b.Common(ACCOUNT, d, recorded, sequence=self.day)

    def state(self) -> LedgerState:
        return replay(self.txs)

    def run(self, case: dict[str, Any], step: dict[str, Any]) -> dict[str, Decimal]:
        inst = InstrumentId.parse(case["instrument"])
        cur = Currency(case["currency"])
        params = case.get("parameters", {})
        action = step["action"]
        obs: dict[str, Decimal] = {}

        if action in ("buy", "sell"):
            side = TxType.BUY if action == "buy" else TxType.SELL
            fx = dec(step["fx_rate"]) if "fx_rate" in step else None
            tx = b.trade(
                self.common(),
                side,
                inst,
                cur,
                dec(step["quantity"]),
                dec(step["price"]),
                fee=dec(step.get("fee", "0")),
                fx_rate_try=fx,
            )
            self.txs.append(tx)
            state = self.state()
            if side is TxType.SELL:
                r = state.realizations[-1]
                obs |= {
                    "net_proceeds": r.proceeds,
                    "cost_of_sold": r.cost,
                    "realized_pnl": r.realized,
                }
        elif action == "bonus_issue":
            c = self.common()
            ca = CorporateAction(
                inst,
                CorporateActionType.BONUS_ISSUE,
                c.trade_date,
                "reference",
                c.recorded_at,
                ratio=dec(step["ratio"]),
            )
            held = self.state().quantity_before(ACCOUNT, inst, c.trade_date)
            self.txs.append(b.share_count_action(c, ca, cur, held * (ca.share_factor - 1)))
            state = self.state()
            obs["pre_ex_price_factor"] = price_adjustment_factor(ca)
        elif action == "cash_dividend":
            c = self.common()
            ca = CorporateAction(
                inst,
                CorporateActionType.CASH_DIVIDEND,
                c.trade_date,
                "reference",
                c.recorded_at,
                amount_per_share=dec(step["amount_per_share"]),
                currency=cur,
            )
            eligible = self.state().quantity_before(ACCOUNT, inst, c.trade_date)
            gross, wht, _ = b.dividend_amounts(
                eligible, ca.amount_per_share or Decimal(0), dec(params["withholding_rate"]), cur
            )
            self.txs.append(b.cash_dividend(c, ca, gross.amount, wht.amount))
            state = self.state()
            ev = state.income[-1]
            obs |= {"gross": ev.gross, "withholding": ev.withholding, "net": ev.net}
        elif action == "value":
            state = self.state()
            pos = state.position(ACCOUNT, inst)
            assert pos is not None
            v = value_position(
                ACCOUNT,
                inst,
                cur,
                pos.quantity,
                pos.cost,
                pos.cost_try,
                dec(step["price"]),
                dec(step["fx_rate"]),
            )
            assert v.value is not None
            assert v.value_try is not None
            assert v.unrealized is not None
            assert v.unrealized_try is not None
            assert v.fx_effect_try is not None
            assert v.price_effect_try is not None
            obs |= {
                "value": v.value,
                "value_try": v.value_try,
                "unrealized_pnl": v.unrealized,
                "unrealized_pnl_try": v.unrealized_try,
                "fx_effect_try": v.fx_effect_try,
                "price_effect_try": v.price_effect_try,
            }
        else:
            pytest.fail(f"reference harness does not know action {action!r}")

        pos = state.position(ACCOUNT, inst)
        if pos is not None:
            obs |= {"quantity": pos.quantity, "cost": pos.cost, "cost_try": pos.cost_try}
            if pos.avg_cost is not None:
                obs["avg_cost"] = pos.avg_cost
        return obs


def _check(case_name: str, step_no: int, expect: dict[str, Any], obs: dict[str, Decimal]) -> None:
    for key, raw in expect.items():
        assert key in obs, f"{case_name} step {step_no}: harness has no observation for {key!r}"
        assert obs[key] == dec(raw), (
            f"{case_name} step {step_no}: {key} = {obs[key]}, expected {raw}"
        )


def _run_chain(name: str, runner: Runner, *, check: bool) -> None:
    case = CASES[name]
    if "continues" in case:
        _run_chain(case["continues"], runner, check=False)
    for i, step in enumerate(case["steps"], start=1):
        obs = runner.run(case, step)
        if check and "expect" in step:
            _check(name, i, step["expect"], obs)


LEDGER_CASES = [n for n, c in CASES.items() if "steps" in c]


@pytest.mark.parametrize("name", LEDGER_CASES)
def test_ledger_case(name: str) -> None:
    _run_chain(name, Runner(), check=True)


def test_case_e_twr_and_xirr() -> None:
    case = CASES["e_twr_and_xirr"]
    assert case["day_count"] == "actual/365"
    events = case["events"]
    subs = [
        sub_period_return(dec(start["value_after_flow"]), dec(end["value_before_flow"]))
        for start, end in pairwise(events)
    ]
    exp = case["expect"]
    assert subs == [dec(x) for x in exp["sub_period_returns"]]
    assert twr(subs) == dec(exp["twr"])
    flows = [(date.fromisoformat(e["date"]), -dec(e["flow"])) for e in events if "flow" in e]
    last = events[-1]
    flows.append((date.fromisoformat(last["date"]), dec(last["value_before_flow"])))
    result = xirr(flows)
    assert abs(result - float(dec(exp["xirr"]["value"]))) <= float(dec(exp["xirr"]["tolerance"]))


def test_case_f_real_return() -> None:
    case = CASES["f_real_return"]
    inputs, exp = case["inputs"], case["expect"]["real_return"]
    result = real_return(dec(inputs["nominal_return"]), dec(inputs["inflation"]))
    assert abs(result - dec(exp["value"])) <= dec(exp["tolerance"])


def test_every_case_is_covered() -> None:
    covered: set[str] = set(LEDGER_CASES) | {"e_twr_and_xirr", "f_real_return"}
    assert set(CASES) == covered
