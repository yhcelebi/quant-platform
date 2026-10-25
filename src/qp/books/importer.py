"""Statement rows -> balanced transactions (ADR 0017, 3-4): rules categorize, duplicates and
the second side of transfers between own accounts are skipped, quick adds for cash."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from beancount.core import data
from beancount.core.amount import Amount

from qp.books.config import Rules, Source, fold
from qp.books.ledger import Books, units
from qp.books.reader import Row
from qp.core import QPValidationError

UNCATEGORIZED_OUT = "Expenses:Uncategorized"
UNCATEGORIZED_IN = "Income:Uncategorized"
FEES = "Expenses:Fees:Bank"
TRANSFER_DAYS = 3
REVIEW_FLAG = "!"


def import_id(source: str, row: Row, ordinal: int) -> str:
    """Stable id of a statement line: the same line in an overlapping statement gets the
    same id. ``ordinal`` separates identical lines (same day, amount, text and balance)."""
    key = f"{source}|{row.day}|{row.amount}|{fold(row.description)}|{row.balance}|{ordinal}"
    return f"{source}:{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def _money(x: Decimal, currency: str) -> Amount:
    return Amount(x, currency)


def _posting(account: str, number: Decimal, currency: str) -> data.Posting:
    return data.Posting(account, _money(number, currency), None, None, None, None)


@dataclass
class ImportPlan:
    source: str
    new: list[data.Transaction] = field(default_factory=list)
    duplicates: int = 0
    transfers: list[str] = field(default_factory=list)  # second sides already recorded
    uncategorized: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def categorized(self) -> int:
        return len(self.new) - self.uncategorized


def _transfer_already_recorded(
    books: Books, account: str, amount: Decimal, currency: str, day: date, source: str
) -> data.Transaction | None:
    """A transaction from *another* source that already moved ``amount`` on ``account``
    within ``TRANSFER_DAYS``: this line is the other side of that transfer."""
    for t in books.transactions():
        if abs((t.date - day).days) > TRANSFER_DAYS or t.meta.get("source") == source:
            continue
        for p in t.postings:
            if p.account == account and units(p) == (amount, currency):
                return t
    return None


def plan_import(
    books: Books, source: Source, rows: list[Row], rules: Rules, *, skipped: list[str] | None = None
) -> ImportPlan:
    if source.account not in books.accounts():
        raise QPValidationError(f"{source.account} is not open in accounts.beancount")
    plan = ImportPlan(source.name, skipped=list(skipped or []))
    seen = books.import_ids()
    used: set[int] = set()
    ordinals: Counter[tuple[date, Decimal, str, Decimal | None]] = Counter()
    for row in rows:
        key = (row.day, row.amount, fold(row.description), row.balance)
        iid = import_id(source.name, row, ordinals[key])
        ordinals[key] += 1
        if iid in seen:
            plan.duplicates += 1
            continue
        other = _transfer_already_recorded(
            books, source.account, row.amount, source.currency, row.day, source.name
        )
        if other is not None and id(other) not in used:
            used.add(id(other))
            plan.transfers.append(
                f"{row.day} {row.amount} {source.currency}: recorded on {other.date} "
                f"({other.meta.get('source', 'manual')})"
            )
            continue
        rule = rules.match(source.name, row.description, row.amount)
        if rule is not None:
            counter, payee, tags, flag = rule.account, rule.payee, set(rule.tags), "*"
        else:
            counter = UNCATEGORIZED_OUT if row.amount < 0 else UNCATEGORIZED_IN
            payee, tags, flag = None, set(), REVIEW_FLAG
            plan.uncategorized += 1
        if counter not in books.accounts():
            raise QPValidationError(f"rule account {counter} is not open in accounts.beancount")
        postings = [
            _posting(source.account, row.amount - row.fee, source.currency),
            _posting(counter, -row.amount, source.currency),
        ]
        if row.fee:
            postings.append(_posting(FEES, row.fee, source.currency))
        plan.new.append(
            data.Transaction(
                {"import_id": iid, "source": source.name},
                row.day,
                flag,
                payee,
                row.description,
                frozenset(tags),
                frozenset(),
                postings,
            )
        )
    return plan


def resolve_account(books: Books, name: str, *roots: str) -> str:
    """'groceries', 'food:groceries', 'akbank' or a full name -> one open account under
    ``roots`` (suffix match on whole segments, folded; then prefix of the last segment)."""
    opened = books.accounts()
    if name in opened:
        return name
    want = fold(name).replace(" ", "")
    cands = [a for a in opened if a.startswith(tuple(r + ":" for r in roots))]

    def key(a: str) -> str:
        return fold(a).replace(" ", "")

    found = [a for a in cands if key(a).endswith(":" + want)]
    if not found and ":" not in want:  # a whole segment: "cash" -> Assets:Cash:Wallet
        found = [a for a in cands if want in key(a).split(":")[1:]]
    if not found:
        found = [a for a in cands if key(a).split(":")[-1].startswith(want.split(":")[-1])]
    if len(found) == 1:
        return found[0]
    if not found:
        raise QPValidationError(f"no {'/'.join(roots)} account matches {name!r}")
    raise QPValidationError(f"{name!r} is ambiguous: {', '.join(sorted(found))}")


def quick_add(
    books: Books,
    *,
    day: date,
    amount: Decimal,
    category: str,
    payee: str,
    paid_from: str = "Assets:Cash:Wallet",
    currency: str = "TRY",
    note: str = "",
    tags: tuple[str, ...] = (),
) -> data.Transaction:
    """A spending (``amount`` > 0) or income (``category`` under Income) entered by hand."""
    if amount <= 0:
        raise QPValidationError("amount must be positive")
    account = resolve_account(books, category, "Expenses", "Income")
    source_account = resolve_account(books, paid_from, "Assets", "Liabilities")
    signed = amount if account.startswith("Income:") else -amount
    key = f"manual|{day}|{amount}|{currency}|{account}|{fold(payee)}|{fold(note)}"
    n = sum(
        1 for t in books.transactions() if str(t.meta.get("import_id", "")).startswith("manual:")
    )
    iid = f"manual:{hashlib.sha256(f'{key}|{n}'.encode()).hexdigest()[:16]}"
    return data.Transaction(
        {"import_id": iid, "source": "manual"},
        day,
        "*",
        payee or None,
        note,
        frozenset(tags),
        frozenset(),
        [
            _posting(source_account, signed, currency),
            _posting(account, -signed, currency),
        ],
    )


def recategorize(
    t: data.Transaction, books: Books, account: str, *, payee: str | None = None
) -> data.Transaction:
    """The same transaction with its main category posting (the uncategorized one, else the
    largest) moved to ``account``; fee postings stay; the review flag is cleared."""
    target = resolve_account(books, account, "Expenses", "Income")
    cats = [p for p in t.postings if _is_category(p.account)]
    if not cats:
        raise QPValidationError("this transaction has no category posting to change")
    unc = [p for p in cats if p.account in (UNCATEGORIZED_IN, UNCATEGORIZED_OUT)]
    main = unc[0] if unc else max(cats, key=lambda p: abs(units(p)[0]))
    postings = [p._replace(account=target) if p is main else p for p in t.postings]
    return t._replace(postings=postings, flag="*", payee=payee if payee is not None else t.payee)


def _is_category(account: str) -> bool:
    return account.startswith(("Expenses:", "Income:"))


def needs_review(t: data.Transaction) -> bool:
    return t.flag == REVIEW_FLAG or any(
        p.account in (UNCATEGORIZED_IN, UNCATEGORIZED_OUT) for p in t.postings
    )
