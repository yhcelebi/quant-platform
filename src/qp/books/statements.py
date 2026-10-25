"""Income statement, balance sheet, cash flow statement and budgets in TRY (ADR 0017, 5-6).

Amounts in other currencies use the TCMB rate of the transaction date (flows) or of the
report date (balances), supplied by ``rates``. A missing rate is reported and the amount is
left out of totals; nothing is estimated. Exact Decimal throughout; results rounded to kuruş.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from beancount.core import data

from qp.books.ledger import Books, units

type Rates = Callable[[str, date], Decimal | None]

ZERO = Decimal(0)
CENT = Decimal("0.01")
BASE = "TRY"
CASH_PREFIXES = ("Assets:Bank:", "Assets:Wallet:", "Assets:Cash:")


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT)


@dataclass
class Converter:
    rates: Rates
    missing: set[str] = field(default_factory=set)

    def __call__(self, number: Decimal, currency: str, day: date) -> Decimal | None:
        if currency == BASE:
            return number
        rate = self.rates(currency, day)
        if rate is None:
            self.missing.add(f"{currency} on {day}")
            return None
        return number * rate


def _in(t: data.Transaction, start: date, end: date) -> bool:
    return start <= t.date <= end


def group_of(account: str, depth: int = 2) -> str:
    return ":".join(account.split(":")[:depth])


# --------------------------------------------------------------------------- income statement


@dataclass
class IncomeStatement:
    start: date
    end: date
    income: dict[str, Decimal] = field(default_factory=dict)  # positive
    expenses: dict[str, Decimal] = field(default_factory=dict)  # positive
    missing_rates: list[str] = field(default_factory=list)

    @property
    def total_income(self) -> Decimal:
        return sum(self.income.values(), ZERO)

    @property
    def total_expenses(self) -> Decimal:
        return sum(self.expenses.values(), ZERO)

    @property
    def net(self) -> Decimal:
        return self.total_income - self.total_expenses

    @property
    def savings_rate(self) -> Decimal | None:
        return self.net / self.total_income if self.total_income else None

    def groups(self, side: str, depth: int = 2) -> dict[str, Decimal]:
        src = self.income if side == "income" else self.expenses
        out: dict[str, Decimal] = defaultdict(Decimal)
        for a, v in src.items():
            out[group_of(a, depth)] += v
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def income_statement(
    txns: Iterable[data.Transaction], start: date, end: date, rates: Rates
) -> IncomeStatement:
    conv = Converter(rates)
    inc: dict[str, Decimal] = defaultdict(Decimal)
    exp: dict[str, Decimal] = defaultdict(Decimal)
    for t in txns:
        if not _in(t, start, end):
            continue
        for p in t.postings:
            if not p.account.startswith(("Income:", "Expenses:")):
                continue
            v = conv(*units(p), t.date)
            if v is None:
                continue
            if p.account.startswith("Income:"):
                inc[p.account] -= v
            else:
                exp[p.account] += v
    return IncomeStatement(
        start,
        end,
        {a: _q(v) for a, v in sorted(inc.items()) if v},
        {a: _q(v) for a, v in sorted(exp.items()) if v},
        sorted(conv.missing),
    )


# --------------------------------------------------------------------------- balances


def balances(txns: Iterable[data.Transaction], day: date) -> dict[str, dict[str, Decimal]]:
    """Account -> currency -> balance at the end of ``day`` (units)."""
    out: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for t in txns:
        if t.date > day:
            continue
        for p in t.postings:
            number, currency = units(p)
            out[p.account][currency] += number
    return {a: {c: v for c, v in cur.items() if v} for a, cur in out.items()}


@dataclass(frozen=True, slots=True)
class Line:
    account: str
    native: dict[str, Decimal]  # balance per currency
    value: Decimal | None  # TRY at the report date; None if a rate is missing


@dataclass
class BalanceSheet:
    day: date
    assets: list[Line] = field(default_factory=list)
    liabilities: list[Line] = field(default_factory=list)  # value positive = owed
    opening_equity: Decimal = ZERO
    retained: Decimal = ZERO  # income - expenses to date, at transaction-date rates
    missing_rates: list[str] = field(default_factory=list)

    @property
    def total_assets(self) -> Decimal:
        return sum((x.value for x in self.assets if x.value is not None), ZERO)

    @property
    def total_liabilities(self) -> Decimal:
        return sum((x.value for x in self.liabilities if x.value is not None), ZERO)

    @property
    def net_worth(self) -> Decimal:
        return self.total_assets - self.total_liabilities

    @property
    def revaluation(self) -> Decimal:
        """Net worth not explained by opening equity and retained income: the change in TRY
        value of foreign-currency balances (and conversion differences) since booking."""
        return self.net_worth - self.opening_equity - self.retained


def balance_sheet(txns: Sequence[data.Transaction], day: date, rates: Rates) -> BalanceSheet:
    conv = Converter(rates)
    sheet = BalanceSheet(day)
    for account, cur in sorted(balances(txns, day).items()):
        if not cur:
            continue
        values = [conv(v, c, day) for c, v in cur.items()]
        value = (
            None
            if any(v is None for v in values)
            else sum((v for v in values if v is not None), ZERO)
        )
        if account.startswith("Assets:"):
            sheet.assets.append(Line(account, cur, None if value is None else _q(value)))
        elif account.startswith("Liabilities:"):
            sheet.liabilities.append(
                Line(
                    account, {c: -v for c, v in cur.items()}, None if value is None else _q(-value)
                )
            )
    for t in txns:  # opening equity at the rates of its booking dates, like retained income
        if t.date > day:
            continue
        for p in t.postings:
            if p.account.startswith("Equity:Opening"):
                v = conv(*units(p), t.date)
                sheet.opening_equity -= v or ZERO
    stmt = income_statement(txns, date.min, day, rates)
    sheet.retained = stmt.net
    sheet.opening_equity = _q(sheet.opening_equity)
    sheet.missing_rates = sorted(conv.missing | set(stmt.missing_rates))
    return sheet


# --------------------------------------------------------------------------- cash flow


def is_cash(account: str) -> bool:
    return account.startswith(CASH_PREFIXES)


def flow_class(account: str) -> str:
    """Which section of the cash flow statement a counter-account belongs to."""
    if account.startswith(("Income:", "Expenses:", "Liabilities:CreditCard")):
        return "operating"
    if account.startswith(("Assets:Investments", "Assets:Property", "Assets:Vehicle")):
        return "investing"
    if account.startswith(("Liabilities:", "Assets:Receivable")):
        return "financing"
    if account.startswith("Equity:Opening"):
        return "opening"
    if account.startswith("Equity:Conversions"):
        return "exchange"
    return "other"


@dataclass
class CashFlow:
    start: date
    end: date
    opening_cash: Decimal = ZERO
    closing_cash: Decimal = ZERO
    lines: dict[str, dict[str, Decimal]] = field(default_factory=dict)  # section -> group -> TRY
    missing_rates: list[str] = field(default_factory=list)

    def section(self, name: str) -> Decimal:
        return sum(self.lines.get(name, {}).values(), ZERO)

    @property
    def net_flow(self) -> Decimal:
        return sum((self.section(s) for s in self.lines), ZERO)

    @property
    def revaluation(self) -> Decimal:
        """Change in TRY value of foreign cash between booking and the report dates."""
        return self.closing_cash - self.opening_cash - self.net_flow


def _cash_value(txns: Sequence[data.Transaction], day: date, conv: Converter) -> Decimal:
    total = ZERO
    for account, cur in balances(txns, day).items():
        if is_cash(account):
            for c, v in cur.items():
                x = conv(v, c, day)
                total += x or ZERO
    return total


def cash_flow(txns: Sequence[data.Transaction], start: date, end: date, rates: Rates) -> CashFlow:
    """Direct method on cash accounts. Each transaction's cash movement is attributed to its
    non-cash postings by their TRY amounts; moves between own cash accounts net to zero."""
    conv = Converter(rates)
    out = CashFlow(start, end)
    lines: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for t in txns:
        if not _in(t, start, end) or not any(is_cash(p.account) for p in t.postings):
            continue
        for p in t.postings:
            if is_cash(p.account):
                continue
            v = conv(*units(p), t.date)
            if v is None:
                continue
            section = flow_class(p.account)
            lines[section][group_of(p.account, 3 if section == "investing" else 2)] -= v
    out.lines = {s: {g: _q(v) for g, v in sorted(gs.items()) if v} for s, gs in lines.items()}
    out.opening_cash = _q(_cash_value(txns, start - timedelta(days=1), conv))
    out.closing_cash = _q(_cash_value(txns, end, conv))
    out.missing_rates = sorted(conv.missing)
    return out


# --------------------------------------------------------------------------- budgets

PERIODS = ("daily", "weekly", "monthly", "quarterly", "yearly")


@dataclass(frozen=True, slots=True)
class Budget:
    account: str
    period: str
    amount: Decimal
    currency: str
    start: date


def budgets(entries: Iterable[Any]) -> list[Budget]:
    out = []
    for e in entries:
        if isinstance(e, data.Custom) and e.type == "budget" and len(e.values) >= 3:
            account, period, amt = (v.value for v in e.values[:3])
            if period in PERIODS:
                out.append(Budget(str(account), str(period), amt.number, amt.currency, e.date))
    return sorted(out, key=lambda b: (b.account, b.start))


def _per_day(b: Budget, d: date) -> Decimal:
    if b.period == "daily":
        return b.amount
    if b.period == "weekly":
        return b.amount / 7
    if b.period == "monthly":
        return b.amount / calendar.monthrange(d.year, d.month)[1]
    if b.period == "quarterly":
        q0 = date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
        q1 = date(d.year + (q0.month == 10), (q0.month + 2) % 12 + 1, 1)
        return b.amount / (q1 - q0).days
    return b.amount / (366 if calendar.isleap(d.year) else 365)


def budget_amount(items: Sequence[Budget], start: date, end: date) -> Decimal | None:
    """Sum over the days of [start, end] of the budget in force each day (the latest entry
    on or before that day), pro-rated by days. None if no budget applies on any day."""
    items = sorted(items, key=lambda b: b.start)
    total, any_day = ZERO, False
    d = start
    while d <= end:
        active = [b for b in items if b.start <= d]
        if active:
            total += _per_day(active[-1], d)
            any_day = True
        d += timedelta(days=1)
    return _q(total) if any_day else None


@dataclass(frozen=True, slots=True)
class BudgetLine:
    account: str
    period: str  # of the latest budget entry
    budget: Decimal
    actual: Decimal
    currency: str

    @property
    def remaining(self) -> Decimal:
        return self.budget - self.actual

    @property
    def used(self) -> Decimal | None:
        return self.actual / self.budget if self.budget else None

    @property
    def status(self) -> str:
        u = self.used
        if u is None:
            return "no budget"
        return "over" if u > 1 else "warning" if u >= Decimal("0.8") else "ok"


def budget_report(
    books: Books, start: date, end: date, rates: Rates
) -> tuple[list[BudgetLine], list[str]]:
    """Budget vs actual for every budgeted account over [start, end]; a group budget
    (Expenses:Food) counts all its subaccounts. Budgets must be in TRY."""
    stmt = income_statement(books.transactions(), start, end, rates)
    by_account: dict[str, list[Budget]] = defaultdict(list)
    for b in budgets(books.entries):
        by_account[b.account].append(b)
    lines = []
    notes = list(stmt.missing_rates)
    for account, items in sorted(by_account.items()):
        if any(b.currency != BASE for b in items):
            notes.append(f"{account}: budgets must be in {BASE}")
            continue
        amount = budget_amount(items, start, end)
        if amount is None:
            continue
        actual = sum(
            (v for a, v in stmt.expenses.items() if a == account or a.startswith(account + ":")),
            ZERO,
        )
        lines.append(BudgetLine(account, items[-1].period, amount, actual, BASE))
    return lines, notes
