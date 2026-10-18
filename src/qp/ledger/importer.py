"""Canonical ledger CSV import (docs/ledger-csv-format.md, ADR 0006).

``plan_import`` is pure: it parses and validates a file, builds transactions with the
canonical builders, and simulates them on top of the existing ledger. Nothing is written.
A plan with no errors is committed by :func:`commit_import` in one database transaction.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from qp.core import Currency, InstrumentId, Money, QPError, parse_utc, to_decimal
from qp.ledger import builders as b
from qp.ledger.domain import Account, LedgerError, Transaction, TxType
from qp.ledger.engine import LedgerState, replay
from qp.ledger.repo import LedgerRepository
from qp.reference.domain import CorporateAction, CorporateActionType, Instrument
from qp.reference.rates import RateTable
from qp.reference.repo import ReferenceRepository

COLUMNS = (
    "account", "type", "trade_date", "trade_time", "settle_date", "instrument", "quantity",
    "price", "currency", "fee", "fee_currency", "amount", "fx_rate_try", "to_currency",
    "to_amount", "ratio", "ex_date", "amount_per_share", "gross", "withholding",
    "withholding_rate", "net", "new_instrument", "transfer_id", "known_time", "external_ref",
    "note",
)  # fmt: skip
REQUIRED = ("account", "type", "trade_date")
ZERO = Decimal(0)


class Lookup(Protocol):
    """What the importer needs to know about existing reference and ledger data."""

    def account(self, code: str) -> Account | None: ...
    def instrument(self, instrument_id: InstrumentId) -> Instrument | None: ...
    def corporate_action(
        self, instrument: InstrumentId, action_type: CorporateActionType, ex_date: date
    ) -> CorporateAction | None: ...


class RowError(QPError):
    pass


@dataclass
class ImportPlan:
    file_name: str
    sha256: str
    row_count: int = 0
    transactions: list[Transaction] = field(default_factory=list)
    new_actions: list[CorporateAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    before: LedgerState | None = None
    after: LedgerState | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


class _Row:
    def __init__(self, line: int, raw: dict[str, str], lookup: Lookup) -> None:
        self.line = line
        self.raw = {k: (v or "").strip() for k, v in raw.items() if k is not None}
        self.lookup = lookup

    def has(self, name: str) -> bool:
        return bool(self.raw.get(name))

    def text(self, name: str, *, required: bool = True) -> str:
        value = self.raw.get(name, "")
        if required and not value:
            raise RowError(f"column '{name}' is required for type {self.raw.get('type')}")
        return value

    def dec(self, name: str, *, required: bool = True, default: Decimal | None = None) -> Decimal:
        if not self.has(name):
            if required and default is None:
                raise RowError(f"column '{name}' is required for type {self.raw.get('type')}")
            return default if default is not None else ZERO
        try:
            return to_decimal(self.raw[name])
        except QPError as exc:
            raise RowError(f"column '{name}': {exc}") from exc

    def opt_dec(self, name: str) -> Decimal | None:
        return self.dec(name) if self.has(name) else None

    def day(self, name: str, *, required: bool = True) -> date | None:
        if not self.has(name):
            if required:
                raise RowError(f"column '{name}' is required")
            return None
        try:
            return date.fromisoformat(self.raw[name])
        except ValueError as exc:
            raise RowError(f"column '{name}': expected YYYY-MM-DD, got {self.raw[name]!r}") from exc

    def instant(self, name: str) -> datetime | None:
        if not self.has(name):
            return None
        try:
            return parse_utc(self.raw[name])
        except (QPError, ValueError) as exc:
            raise RowError(
                f"column '{name}': expected ISO 8601 with offset (e.g. 2025-01-02T10:15:00+03:00)"
            ) from exc

    def currency(self, name: str = "currency", *, default: Currency | None = None) -> Currency:
        if not self.has(name):
            if default is None:
                raise RowError(f"column '{name}' is required for type {self.raw.get('type')}")
            return default
        try:
            return Currency(self.raw[name])
        except QPError as exc:
            raise RowError(str(exc)) from exc

    def instrument(self, name: str = "instrument") -> Instrument:
        try:
            iid = InstrumentId.parse(self.text(name))
        except QPError as exc:
            raise RowError(f"column '{name}': {exc}") from exc
        inst = self.lookup.instrument(iid)
        if inst is None:
            raise RowError(f"unknown instrument {iid}; add it with `qp ref import` first")
        return inst

    def instrument_currency(self, inst: Instrument) -> Currency:
        cur = self.currency(default=inst.currency)
        if cur != inst.currency:
            raise RowError(f"{inst.id} trades in {inst.currency}, row says {cur}")
        return cur


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def plan_import(
    content: bytes,
    file_name: str,
    lookup: Lookup,
    existing: list[Transaction],
    *,
    now: datetime,
    existing_refs: set[tuple[str, str]] | None = None,
    batch_exists: Callable[[str], bool] | None = None,
    rates: RateTable | None = None,
) -> ImportPlan:
    """Parse, validate and simulate an import. Never writes anything."""
    plan = ImportPlan(file_name=file_name, sha256=_sha256(content))
    if batch_exists is not None and batch_exists(plan.sha256):
        plan.errors.append("this exact file was already imported (same SHA-256)")
        return plan
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        plan.errors.append("file is not UTF-8")
        return plan
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    unknown = sorted(set(header) - set(COLUMNS))
    missing = sorted(set(REQUIRED) - set(header))
    if unknown or missing:
        plan.errors.append(f"header: unknown columns {unknown}, missing required {missing}")
        return plan
    reader.fieldnames = header

    refs = set(existing_refs or set())
    actions: dict[tuple[InstrumentId, CorporateActionType, date], CorporateAction] = {}
    built: list[Transaction] = []
    ctx = _Context(lookup, existing, built, actions, plan, file_name, now, rates or RateTable([]))
    for line, raw in enumerate(reader, start=2):
        plan.row_count += 1
        row = _Row(line, raw, lookup)
        try:
            tx = ctx.build(row)
        except (QPError, ValueError, ArithmeticError) as exc:
            plan.errors.append(f"line {line}: {exc}")
            continue
        if tx.external_ref:
            key = (tx.account, tx.external_ref)
            if key in refs:
                plan.errors.append(
                    f"line {line}: external_ref {tx.external_ref!r} already used in {tx.account}"
                )
                continue
            refs.add(key)
        built.append(tx)
    if plan.row_count == 0:
        plan.errors.append("file has no data rows")
    plan.transactions = built
    plan.new_actions = [a for a in actions.values() if a.id is None]
    if plan.errors:
        return plan
    try:
        plan.before = replay(existing)
        plan.after = replay([*existing, *built])
    except LedgerError as exc:
        plan.errors.append(f"simulation: {exc}")
        return plan
    known = set(plan.before.warnings)
    plan.warnings.extend(w for w in plan.after.warnings if w not in known)
    return plan


@dataclass
class _Context:
    lookup: Lookup
    existing: list[Transaction]
    built: list[Transaction]
    actions: dict[tuple[InstrumentId, CorporateActionType, date], CorporateAction]
    plan: ImportPlan
    file_name: str
    now: datetime
    rates: RateTable

    def held_before(self, account: str, instrument: InstrumentId, day: date) -> Decimal:
        state = replay([*self.existing, *self.built], as_of=day - timedelta(days=1))
        return state.quantity_before(account, instrument, day)

    def action(self, row: _Row, ca: CorporateAction) -> CorporateAction:
        key = (ca.instrument, ca.action_type, ca.ex_date)
        if key in self.actions:
            known = self.actions[key]
        else:
            stored = self.lookup.corporate_action(*key)
            known = stored if stored is not None else ca
            self.actions[key] = known
        if not known.same_terms(ca):
            raise RowError(
                f"{ca.action_type} {ca.instrument} ex {ca.ex_date} is already recorded with "
                "different terms; a correction needs a new ADR-defined process"
            )
        return known

    def build(self, row: _Row) -> Transaction:
        account = row.text("account")
        if self.lookup.account(account) is None:
            raise RowError(f"unknown account {account!r}; add it with `qp ledger account add`")
        try:
            tx_type = TxType(row.text("type"))
        except ValueError as exc:
            raise RowError(f"unknown type {row.raw.get('type')!r}") from exc
        if tx_type is TxType.REVERSAL:
            raise RowError("reversals are not imported; use `qp ledger reverse <id>`")
        trade_date = row.day("trade_date")
        assert trade_date is not None
        c = b.Common(
            account=account,
            trade_date=trade_date,
            recorded_at=self.now,
            sequence=row.line,
            trade_time=row.instant("trade_time"),
            settle_date=row.day("settle_date", required=False),
            external_ref=row.text("external_ref", required=False) or None,
            note=row.text("note", required=False),
        )
        handler = getattr(self, f"_{tx_type.value}")
        tx: Transaction = handler(row, c, tx_type)
        return tx

    # --------------------------------------------------------------- per type
    def _opening_balance(self, row: _Row, c: b.Common, _: TxType) -> Transaction:
        if row.has("instrument"):
            inst = row.instrument()
            cur = row.instrument_currency(inst)
            return b.opening_position(
                c,
                inst.id,
                cur,
                row.dec("quantity"),
                row.dec("amount"),
                fx_rate_try=row.opt_dec("fx_rate_try"),
            )
        return b.opening_cash(c, row.currency(), row.dec("amount"))

    def _cash(self, row: _Row, c: b.Common, t: TxType) -> Transaction:
        return b.cash_flow(
            c,
            t,
            row.currency(),
            row.dec("amount"),
            withholding=row.dec("withholding", required=False),
            fx_rate_try=row.opt_dec("fx_rate_try"),
        )

    _deposit = _withdrawal = _fee = _tax = _interest = _cash

    def _trade(self, row: _Row, c: b.Common, t: TxType) -> Transaction:
        inst = row.instrument()
        cur = row.instrument_currency(inst)
        return b.trade(
            c,
            t,
            inst.id,
            cur,
            row.dec("quantity"),
            row.dec("price"),
            fee=row.dec("fee", required=False),
            fee_currency=row.currency("fee_currency", default=cur),
            fx_rate_try=row.opt_dec("fx_rate_try"),
        )

    _buy = _sell = _trade

    def _fx_conversion(self, row: _Row, c: b.Common, _: TxType) -> Transaction:
        return b.fx_conversion(
            c, row.currency(), row.dec("amount"), row.currency("to_currency"), row.dec("to_amount")
        )

    def _transfer(self, row: _Row, c: b.Common, t: TxType) -> Transaction:
        transfer_id = row.text("transfer_id")
        if row.has("instrument"):
            inst = row.instrument()
            return b.securities_transfer(
                c, t, transfer_id, inst.id, row.instrument_currency(inst), row.dec("quantity")
            )
        return b.cash_transfer(c, t, transfer_id, row.currency(), row.dec("amount"))

    _transfer_out = _transfer_in = _transfer

    def _ex_date(self, row: _Row, c: b.Common) -> date:
        ex = row.day("ex_date", required=False) or c.trade_date
        return ex

    def _new_action(
        self, row: _Row, c: b.Common, kind: CorporateActionType, **terms: object
    ) -> CorporateAction:
        known_time = row.instant("known_time") or self.now
        ca = CorporateAction(
            source=f"ledger_import:{self.file_name}",
            known_time=known_time,
            ex_date=self._ex_date(row, c),
            action_type=kind,
            **terms,  # type: ignore[arg-type]
        )
        return self.action(row, ca)

    def _share_count(self, row: _Row, c: b.Common, t: TxType) -> Transaction:
        inst = row.instrument()
        cur = row.instrument_currency(inst)
        ca = self._new_action(
            row, c, CorporateActionType(t.value), instrument=inst.id, ratio=row.dec("ratio")
        )
        if ca.ex_date != c.trade_date:
            raise RowError("share-count actions are booked on the ex-date (trade_date = ex_date)")
        if row.has("quantity"):
            received = row.dec("quantity")
        else:
            held = self.held_before(c.account, inst.id, ca.ex_date)
            received = held * (ca.share_factor - 1)
            if received != received.to_integral_value():
                raise RowError(
                    f"computed {received} new shares is fractional; "
                    "give the actual quantity received"
                )
        return b.share_count_action(c, ca, cur, received)

    _split = _bonus_issue = _stock_dividend = _share_count

    def _rights_subscription(self, row: _Row, c: b.Common, t: TxType) -> Transaction:
        inst = row.instrument()
        cur = row.instrument_currency(inst)
        price = row.dec("price")
        ca = self._new_action(
            row,
            c,
            CorporateActionType.RIGHTS_ISSUE,
            instrument=inst.id,
            ratio=row.dec("ratio"),
            subscription_price=price,
            currency=cur,
        )
        return b.trade(
            c,
            t,
            inst.id,
            cur,
            row.dec("quantity"),
            price,
            fee=row.dec("fee", required=False),
            fee_currency=row.currency("fee_currency", default=cur),
            fx_rate_try=row.opt_dec("fx_rate_try"),
            corporate_action=ca,
        )

    def _cash_dividend(self, row: _Row, c: b.Common, _: TxType) -> Transaction:
        inst = row.instrument()
        cur = row.instrument_currency(inst)
        ex = self._ex_date(row, c)
        ca = self._new_action(
            row,
            c,
            CorporateActionType.CASH_DIVIDEND,
            instrument=inst.id,
            amount_per_share=row.dec("amount_per_share"),
            currency=cur,
            pay_date=c.trade_date if c.trade_date >= ex else None,
        )
        if ca.ex_date > c.trade_date:
            raise RowError("dividend trade_date (pay date) cannot be before ex_date")
        eligible = self.held_before(c.account, inst.id, ex)
        if row.has("quantity") and row.dec("quantity") != eligible:
            raise RowError(
                f"eligible quantity {row.dec('quantity')} != ledger holding {eligible} before {ex}"
            )
        assert ca.amount_per_share is not None
        if row.has("gross"):
            gross = row.dec("gross")
        else:
            gross = Money(eligible * ca.amount_per_share, cur).quantize().amount
        if row.has("withholding"):
            withholding = row.dec("withholding")
        else:
            rate = row.opt_dec("withholding_rate")
            if rate is None:
                key = f"withholding.dividend.{inst.id.venue}.{inst.asset_class.value}"
                found = self.rates.lookup(key, c.trade_date)
                if found is None:
                    raise RowError(
                        f"give 'withholding' or 'withholding_rate' (no configured rate for {key})"
                    )
                rate = found.rate
            if not (ZERO <= rate <= 1):
                raise RowError("withholding_rate must be a fraction in [0, 1]")
            withholding = Money(gross * rate, cur).quantize().amount
        if row.has("net") and row.dec("net") != gross - withholding:
            raise RowError(f"net {row.dec('net')} != gross {gross} - withholding {withholding}")
        return b.cash_dividend(c, ca, gross, withholding, fx_rate_try=row.opt_dec("fx_rate_try"))

    def _merger(self, row: _Row, c: b.Common, _: TxType) -> Transaction:
        old = row.instrument()
        new = row.instrument("new_instrument")
        ca = self._new_action(
            row,
            c,
            CorporateActionType.MERGER,
            instrument=old.id,
            ratio=row.dec("ratio"),
            new_instrument=new.id,
        )
        if ca.ex_date != c.trade_date:
            raise RowError("mergers are booked on the ex-date (trade_date = ex_date)")
        held = self.held_before(c.account, old.id, ca.ex_date)
        if row.has("quantity"):
            received = row.dec("quantity")
        else:
            received = held * ca.share_factor
            if received != received.to_integral_value():
                raise RowError(f"computed {received} new shares is fractional; give 'quantity'")
        return b.merger(c, ca, old.currency, held, new.currency, received)

    def _delisting(self, row: _Row, c: b.Common, _: TxType) -> Transaction:
        inst = row.instrument()
        cur = row.instrument_currency(inst)
        ca = self._new_action(row, c, CorporateActionType.DELISTING, instrument=inst.id)
        held = self.held_before(c.account, inst.id, c.trade_date + timedelta(days=1))
        return b.delisting(
            c,
            ca,
            cur,
            held,
            row.dec("amount", required=False),
            fx_rate_try=row.opt_dec("fx_rate_try"),
        )


# ----------------------------------------------------------------------- database
class DbLookup:
    def __init__(self, session: Session) -> None:
        self.ref = ReferenceRepository(session)
        self.ledger = LedgerRepository(session)
        self._accounts = {a.code: a for a in self.ledger.accounts()}

    def account(self, code: str) -> Account | None:
        return self._accounts.get(code)

    def instrument(self, instrument_id: InstrumentId) -> Instrument | None:
        return self.ref.instrument(instrument_id)

    def corporate_action(
        self, instrument: InstrumentId, action_type: CorporateActionType, ex_date: date
    ) -> CorporateAction | None:
        return self.ref.corporate_action(instrument, action_type, ex_date)


def plan_import_from_db(
    session: Session,
    content: bytes,
    file_name: str,
    *,
    now: datetime,
    rates: RateTable | None = None,
) -> ImportPlan:
    ledger = LedgerRepository(session)
    return plan_import(
        content,
        file_name,
        DbLookup(session),
        ledger.transactions(),
        now=now,
        existing_refs=ledger.external_refs(),
        batch_exists=ledger.batch_exists,
        rates=rates,
    )


def commit_import(session: Session, plan: ImportPlan) -> list[Transaction]:
    """Write a validated plan. The caller owns the transaction (all or nothing)."""
    if not plan.ok:
        raise LedgerError("refusing to commit an import plan with errors")
    ref = ReferenceRepository(session)
    ledger = LedgerRepository(session)
    stored_actions: dict[tuple[InstrumentId, CorporateActionType, date], CorporateAction] = {}
    for new in plan.new_actions:
        key = (new.instrument, new.action_type, new.ex_date)
        stored_actions[key] = ref.add_corporate_action(new)
    resolved: list[Transaction] = []
    for tx in plan.transactions:
        link = tx.corporate_action
        if link is not None and link.id is None:
            stored = stored_actions[(link.instrument, link.action_type, link.ex_date)]
            tx = replace(tx, corporate_action=stored)
        resolved.append(tx)
    batch_id = ledger.add_batch(plan.file_name, plan.sha256, plan.row_count)
    return ledger.insert(resolved, batch_id=batch_id)


__all__ = ["COLUMNS", "ImportPlan", "Lookup", "commit_import", "plan_import", "plan_import_from_db"]
