"""Importer validation, pure (in-memory lookup; no database)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qp.core import TRY, USD, Currency, InstrumentId
from qp.ledger.domain import Account, AccountPurpose, AccountType, Transaction, TxType
from qp.ledger.importer import ImportPlan, plan_import
from qp.reference.domain import AssetClass, CorporateAction, CorporateActionType, Instrument
from qp.reference.rates import DatedRate, RateTable

NOW = datetime(2026, 1, 1, tzinfo=UTC)
THYAO = Instrument(InstrumentId.parse("THYAO.BIST"), TRY, AssetClass.EQUITY)
AAPL = Instrument(InstrumentId.parse("AAPL.NASDAQ"), USD, AssetClass.EQUITY)


class FakeLookup:
    def __init__(self, actions: list[CorporateAction] | None = None) -> None:
        self.actions = {(a.instrument, a.action_type, a.ex_date): a for a in actions or []}

    def account(self, code: str) -> Account | None:
        if code in ("A", "B"):
            return Account(code, code, AccountType.BROKERAGE, AccountPurpose.LONG_TERM, TRY)
        return None

    def instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        return {THYAO.id: THYAO, AAPL.id: AAPL}.get(instrument_id)

    def corporate_action(
        self, instrument: InstrumentId, action_type: CorporateActionType, ex_date: date
    ) -> CorporateAction | None:
        return self.actions.get((instrument, action_type, ex_date))


HEADER_COLUMNS = (
    "account", "type", "trade_date", "instrument", "quantity", "price", "currency", "fee",
    "fee_currency", "amount", "fx_rate_try", "ratio", "ex_date", "amount_per_share", "gross",
    "withholding", "withholding_rate", "net", "transfer_id", "trade_time", "external_ref",
)  # fmt: skip
HEADER = ",".join(HEADER_COLUMNS) + "\n"


def run(
    rows: str,
    *,
    existing: list[Transaction] | None = None,
    lookup: FakeLookup | None = None,
    rates: RateTable | None = None,
    header: str = HEADER,
) -> ImportPlan:
    content = (header + rows).encode()
    return plan_import(
        content, "t.csv", lookup or FakeLookup(), existing or [], now=NOW, rates=rates
    )


def test_valid_file_builds_and_simulates() -> None:
    plan = run(
        "A,deposit,2025-01-01,,,,TRY,,,1000,,,,,,,,,,,d1\n"
        "A,buy,2025-01-02,THYAO.BIST,10,50,,1,,,,,,,,,,,,2025-01-02T10:00:00+03:00,b1\n"
    )
    assert plan.ok, plan.errors
    assert [t.tx_type for t in plan.transactions] == [TxType.DEPOSIT, TxType.BUY]
    assert plan.after is not None
    assert plan.after.cash[("A", TRY)] == Decimal("499")
    assert plan.transactions[1].trade_time == datetime(2025, 1, 2, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("Z,deposit,2025-01-01,,,,TRY,,,1,,,,,,,,,,,\n", "unknown account"),
        ("A,teleport,2025-01-01,,,,TRY,,,1,,,,,,,,,,,\n", "unknown type"),
        ("A,buy,2025-01-01,XXX.BIST,1,1,,,,,,,,,,,,,,,\n", "unknown instrument"),
        ("A,buy,2025-01-01,THYAO.BIST,1,1,USD,,,,,,,,,,,,,,\n", "trades in TRY"),
        ("A,buy,2025-01-01,AAPL.NASDAQ,1,1,,,,,,,,,,,,,,,\n", "needs fx_rate_try"),
        ("A,buy,01/02/2025,THYAO.BIST,1,1,,,,,,,,,,,,,,,\n", "YYYY-MM-DD"),
        ("A,buy,2025-01-01,THYAO.BIST,1,1,,,,,,,,,,,,,,2025-01-01T10:00:00,\n", "with offset"),
        ("A,buy,2025-01-01,THYAO.BIST,,1,,,,,,,,,,,,,,,\n", "'quantity' is required"),
        ("A,buy,2025-01-01,THYAO.BIST,1.5e400x,1,,,,,,,,,,,,,,,\n", "quantity"),
        ("A,sell,2025-01-01,THYAO.BIST,1,1,,,,,,,,,,,,,,,\n", "no THYAO.BIST held"),
        ("A,reversal,2025-01-01,,,,,,,,,,,,,,,,,,\n", "qp ledger reverse"),
    ],
)
def test_row_errors(row: str, message: str) -> None:
    plan = run(row)
    assert not plan.ok
    assert any(message in e for e in plan.errors), plan.errors


def test_header_errors_and_empty_file() -> None:
    assert (
        "unknown columns ['colour']" in run("", header="account,type,trade_date,colour\n").errors[0]
    )
    assert "missing required ['trade_date']" in run("", header="account,type\n").errors[0]
    assert run("").errors == ["file has no data rows"]


def test_one_bad_row_rejects_the_whole_file() -> None:
    plan = run(
        "A,deposit,2025-01-01,,,,TRY,,,1000,,,,,,,,,,,\n"
        "A,deposit,2025-01-01,,,,XXX,,,1000,,,,,,,,,,,\n"
    )
    assert not plan.ok
    assert plan.after is None


def test_duplicate_external_ref_in_file_and_existing() -> None:
    rows = "A,deposit,2025-01-01,,,,TRY,,,1,,,,,,,,,,,r1\n" * 2
    assert any("already used" in e for e in run(rows).errors)
    plan = plan_import(
        (HEADER + rows[: len(rows) // 2]).encode(),
        "t.csv",
        FakeLookup(),
        [],
        now=NOW,
        existing_refs={("A", "r1")},
    )
    assert any("already used" in e for e in plan.errors)


def test_batch_hash_check() -> None:
    plan = plan_import(b"x", "t.csv", FakeLookup(), [], now=NOW, batch_exists=lambda _: True)
    assert plan.errors == ["this exact file was already imported (same SHA-256)"]


def test_bonus_issue_quantity_defaults_from_holdings() -> None:
    plan = run(
        "A,buy,2025-01-02,THYAO.BIST,150,10,,,,,,,,,,,,,,,\n"
        "A,bonus_issue,2025-01-08,THYAO.BIST,,,,,,,,1,2025-01-08,,,,,,,,\n"
    )
    assert plan.ok, plan.errors
    assert plan.after is not None
    pos = plan.after.position("A", THYAO.id)
    assert pos is not None
    assert pos.quantity == 300
    assert len(plan.new_actions) == 1


def test_share_count_action_must_be_on_ex_date() -> None:
    plan = run(
        "A,buy,2025-01-02,THYAO.BIST,150,10,,,,,,,,,,,,,,,\n"
        "A,bonus_issue,2025-01-09,THYAO.BIST,,,,,,,,1,2025-01-08,,,,,,,,\n"
    )
    assert any("ex-date" in e for e in plan.errors)


def test_corporate_action_terms_must_match_existing() -> None:
    known = CorporateAction(
        THYAO.id,
        CorporateActionType.BONUS_ISSUE,
        date(2025, 1, 8),
        "x",
        NOW,
        ratio=Decimal("0.5"),
        id=7,
    )
    plan = run(
        "A,buy,2025-01-02,THYAO.BIST,150,10,,,,,,,,,,,,,,,\n"
        "A,bonus_issue,2025-01-08,THYAO.BIST,,,,,,,,1,2025-01-08,,,,,,,,\n",
        lookup=FakeLookup([known]),
    )
    assert any("different terms" in e for e in plan.errors)
    same = run(
        "A,buy,2025-01-02,THYAO.BIST,150,10,,,,,,,,,,,,,,,\n"
        "A,bonus_issue,2025-01-08,THYAO.BIST,,,,,,,,0.5,2025-01-08,,,,,,,,\n",
        lookup=FakeLookup([known]),
    )
    assert same.ok, same.errors
    assert same.new_actions == []
    assert same.transactions[1].corporate_action == known


def test_dividend_with_rate_amount_and_config() -> None:
    buy = "A,buy,2025-01-02,THYAO.BIST,300,10,,,,,,,,,,,,,,,\n"
    by_rate = run(
        buy + "A,cash_dividend,2025-01-10,THYAO.BIST,,,,,,,,,2025-01-09,0.50,,,0.10,135.00,,,\n"
    )
    assert by_rate.ok, by_rate.errors
    assert by_rate.after is not None
    ev = by_rate.after.income[0]
    assert (ev.gross, ev.withholding, ev.net) == (
        Decimal("150.00"),
        Decimal("15.00"),
        Decimal("135.00"),
    )

    explicit = run(
        buy + "A,cash_dividend,2025-01-10,THYAO.BIST,,,,,,,,,2025-01-09,0.50,150,20,,,,,\n"
    )
    assert explicit.ok, explicit.errors

    missing = run(buy + "A,cash_dividend,2025-01-10,THYAO.BIST,,,,,,,,,2025-01-09,0.50,,,,,,,\n")
    assert any("withholding" in e for e in missing.errors)

    table = RateTable(
        [
            DatedRate(
                "withholding.dividend.BIST.equity",
                Decimal("0.15"),
                date(2024, 12, 22),
                "test source",
            )
        ]
    )
    configured = run(
        buy + "A,cash_dividend,2025-01-10,THYAO.BIST,,,,,,,,,2025-01-09,0.50,,,,,,,\n", rates=table
    )
    assert configured.ok, configured.errors
    assert configured.after is not None
    assert configured.after.income[0].withholding == Decimal("22.50")

    wrong_net = run(
        buy + "A,cash_dividend,2025-01-10,THYAO.BIST,,,,,,,,,2025-01-09,0.50,,,0.10,140,,,\n"
    )
    assert any("net 140" in e for e in wrong_net.errors)


def test_dividend_eligibility_uses_ex_date() -> None:
    plan = run(
        "A,buy,2025-01-02,THYAO.BIST,100,10,,,,,,,,,,,,,,,\n"
        "A,buy,2025-01-09,THYAO.BIST,900,10,,,,,,,,,,,,,,,\n"
        "A,cash_dividend,2025-01-10,THYAO.BIST,,,,,,,,,2025-01-09,1,,,0,,,,\n"
    )
    assert plan.ok, plan.errors
    assert plan.after is not None
    assert plan.after.income[0].gross == Decimal("100.00")


def test_transfers_between_accounts() -> None:
    plan = run(
        "A,buy,2025-01-02,THYAO.BIST,100,10,,,,,,,,,,,,,,,\n"
        "A,transfer_out,2025-01-05,THYAO.BIST,40,,,,,,,,,,,,,,x1,,\n"
        "B,transfer_in,2025-01-05,THYAO.BIST,40,,,,,,,,,,,,,,x1,,\n"
    )
    assert plan.ok, plan.errors
    assert plan.after is not None
    b_pos = plan.after.position("B", THYAO.id)
    assert b_pos is not None
    assert b_pos.cost == Decimal("400")


def test_foreign_buy_with_try_fee() -> None:
    plan = run(
        "A,deposit,2025-01-01,,,,USD,,,100,,,,,,,,,,,\n"
        "A,buy,2025-01-02,AAPL.NASDAQ,1,100,,40,TRY,,40,,,,,,,,,,\n"
    )
    assert plan.ok, plan.errors
    assert plan.after is not None
    pos = plan.after.position("A", AAPL.id)
    assert pos is not None
    assert (pos.cost, pos.cost_try) == (Decimal("101"), Decimal("4040"))
    assert plan.after.cash[("A", Currency("TRY"))] == Decimal("-40")
