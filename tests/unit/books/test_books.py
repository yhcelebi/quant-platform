"""Household books on a hand-calculated month (ADR 0017).

October 2026, opening balances on 1 Oct: Akbank 10,000 TRY, Revolut 500 USD (rate 40).
Akbank: +50,000 salary (2nd), -1,250.50 Migros (3rd), -2,000 to Papara (5th), -300 unknown
(6th), -12.50 EFT fee (7th). Papara: +2,000 from Akbank (5th, the same transfer),
-450 Getir (8th). Revolut: -15.99 USD Netflix (10th, rate 41). USD rate 42 on the 31st.

Income statement: income 50,000; expenses 1,250.50 + 300 + 12.50 + 450 + 15.99 x 41
(655.59) = 2,668.59; net 47,331.41.
Balance sheet 31 Oct: Akbank 56,437.00; Papara 1,550.00; Revolut 484.01 USD x 42 =
20,328.42; net worth 78,315.42 = opening 30,000 + retained 47,331.41 + FX revaluation
984.01 (484.01 USD booked at 40/41, now 42).
Cash flow: opening balances 30,000, operating 47,331.41, the Papara transfer nets to zero,
revaluation 984.01; closing cash 78,315.42.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from qp.books.config import fold, load_rules, load_sources
from qp.books.importer import needs_review, plan_import, quick_add, recategorize
from qp.books.ledger import BooksError, init, load, units, write
from qp.books.reader import parse_amount, parse_date, statement_rows
from qp.books.statements import (
    Budget,
    balance_sheet,
    budget_amount,
    budget_report,
    cash_flow,
    income_statement,
)
from qp.core import QPValidationError

REPO = Path(__file__).resolve().parents[3]
D = Decimal

SOURCES = """
sources:
  akbank:
    account: Assets:Bank:Akbank
    currency: TRY
    decimal: ","
    header_contains: ["Tarih", "Açıklama"]
    date: Tarih
    date_format: "%d.%m.%Y"
    description: [Açıklama]
    amount: Tutar
    balance: Bakiye
  papara:
    account: Assets:Wallet:Papara
    currency: TRY
    decimal: ","
    header_contains: ["Tarih"]
    date: Tarih
    date_format: "%d.%m.%Y %H:%M"
    description: [Açıklama]
    debit: Giden
    credit: Gelen
  revolut:
    account: Assets:Bank:RevolutUS
    currency: USD
    decimal: "."
    header_contains: ["Completed Date", "Amount"]
    date: Completed Date
    date_format: "%Y-%m-%d %H:%M:%S"
    description: [Description]
    amount: Amount
    fee: Fee
    state_column: State
    state_ok: [COMPLETED]
  empty:
    account: Assets:Bank:KuveytTurk
    currency: TRY
"""

AKBANK = """Hesap Hareketleri;;;
Hesap No: 123;;;
Tarih;Açıklama;Tutar;Bakiye
02.10.2026;MAAŞ ÖDEMESİ ACME;50.000,00;60.000,00
03.10.2026;MİGROS TİC A.Ş. İSTANBUL;-1.250,50;58.749,50
05.10.2026;PAPARA YUKLEME;-2.000,00;56.749,50
06.10.2026;XYZ LTD STI;-300,00;56.449,50
07.10.2026;EFT UCRETI;-12,50;56.437,00
;Toplam;;
"""

PAPARA = """Tarih,Açıklama,Gelen,Giden
"05.10.2026 14:02","Akbank'tan para yükleme","2.000,00",""
"08.10.2026 20:15","GETİR","","450,00"
"""

REVOLUT = "\n".join(
    [
        "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance",
        "CARD_PAYMENT,Current,2026-10-10 08:00:00,2026-10-10 09:00:00,Netflix,-15.99,0.00,USD,"
        "COMPLETED,484.01",
        "CARD_PAYMENT,Current,2026-10-11 08:00:00,,Pending shop,-5.00,0.00,USD,PENDING,479.01",
        "",
    ]
)

OPENING = """
2026-10-01 * "Opening balance"
  Assets:Bank:Akbank        10000.00 TRY
  Equity:Opening-Balances
2026-10-01 * "Opening balance"
  Assets:Bank:RevolutUS       500.00 USD
  Equity:Opening-Balances
"""


def rates(currency: str, day: date) -> Decimal | None:
    assert currency == "USD"
    if day >= date(2026, 10, 31):
        return D(42)
    return D(41) if day >= date(2026, 10, 10) else D(40)


@pytest.fixture
def books_dir(tmp_path: Path) -> Path:
    root = tmp_path / "books"
    init(root, REPO / "configs" / "books")
    (root / "sources.yaml").write_text(SOURCES, encoding="utf-8")
    (root / "manual.beancount").write_text(OPENING, encoding="utf-8")
    for name, text in (("akbank.csv", AKBANK), ("papara.csv", PAPARA), ("revolut.csv", REVOLUT)):
        (tmp_path / name).write_text(text, encoding="utf-8")
    return root


def _import(root: Path, name: str, file: Path) -> object:
    books = load(root)
    source = load_sources(root / "sources.yaml")[name]
    rows, skipped = statement_rows(file, source)
    plan = plan_import(books, source, rows, load_rules(root / "rules.yaml"), skipped=skipped)
    write(books, plan.new, message=f"import {name}")
    return plan


def _all(root: Path, tmp: Path) -> None:
    _import(root, "akbank", tmp / "akbank.csv")
    _import(root, "papara", tmp / "papara.csv")
    _import(root, "revolut", tmp / "revolut.csv")


def test_templates_load_cleanly(books_dir: Path) -> None:
    books = load(books_dir)
    assert books.errors == []
    assert "Expenses:Food:Groceries" in books.accounts()
    assert (books_dir / ".git").is_dir()


def test_import_categorizes_skips_totals_and_pending(books_dir: Path, tmp_path: Path) -> None:
    plan = _import(books_dir, "akbank", tmp_path / "akbank.csv")
    assert len(plan.new) == 5  # type: ignore[attr-defined]
    assert plan.uncategorized == 1  # type: ignore[attr-defined]
    assert any("no date" in s for s in plan.skipped)  # type: ignore[attr-defined]
    cats = {
        t.narration: t.postings[1].account
        for t in load(books_dir).transactions()
        if "import_id" in t.meta
    }
    assert cats["MAAŞ ÖDEMESİ ACME"] == "Income:Salary"
    assert cats["MİGROS TİC A.Ş. İSTANBUL"] == "Expenses:Food:Groceries"
    assert cats["PAPARA YUKLEME"] == "Assets:Wallet:Papara"
    assert cats["XYZ LTD STI"] == "Expenses:Uncategorized"
    assert cats["EFT UCRETI"] == "Expenses:Fees:Bank"
    rev = _import(books_dir, "revolut", tmp_path / "revolut.csv")
    assert len(rev.new) == 1  # type: ignore[attr-defined]
    assert any("PENDING" in s for s in rev.skipped)  # type: ignore[attr-defined]


def test_reimport_adds_nothing_and_transfer_is_recorded_once(
    books_dir: Path, tmp_path: Path
) -> None:
    _all(books_dir, tmp_path)
    again = _import(books_dir, "akbank", tmp_path / "akbank.csv")
    assert again.new == []  # type: ignore[attr-defined]
    assert again.duplicates == 5  # type: ignore[attr-defined]
    books = load(books_dir)
    papara = [t for t in books.transactions() if t.meta.get("source") == "papara"]
    assert [t.narration for t in papara] == ["GETİR"]  # the +2,000 was the Akbank transfer
    assert books.errors == []


def test_statements_reconcile_by_hand(books_dir: Path, tmp_path: Path) -> None:
    _all(books_dir, tmp_path)
    txns = load(books_dir).transactions()
    oct1, oct31 = date(2026, 10, 1), date(2026, 10, 31)

    inc = income_statement(txns, oct1, oct31, rates)
    assert inc.total_income == D("50000.00")
    assert inc.expenses["Expenses:Leisure:Subscriptions"] == D("655.59")
    assert inc.total_expenses == D("2668.59")
    assert inc.net == D("47331.41")
    assert inc.groups("expenses")["Expenses:Food"] == D("1700.50")

    bs = balance_sheet(txns, oct31, rates)
    values = {x.account: x.value for x in bs.assets}
    assert values == {
        "Assets:Bank:Akbank": D("56437.00"),
        "Assets:Wallet:Papara": D("1550.00"),
        "Assets:Bank:RevolutUS": D("20328.42"),
    }
    assert bs.net_worth == D("78315.42")
    assert bs.opening_equity == D("30000.00")
    assert bs.retained == D("47331.41")
    assert bs.revaluation == D("984.01")

    cf = cash_flow(txns, oct1, oct31, rates)
    assert cf.opening_cash == D("0.00")
    assert cf.section("opening") == D("30000.00")
    assert cf.section("operating") == D("47331.41")
    assert "investing" not in cf.lines  # the Papara transfer nets to zero
    assert cf.closing_cash == D("78315.42")
    assert cf.revaluation == D("984.01")


def test_budgets_prorate_any_period() -> None:
    daily = [Budget("Expenses:Food", "daily", D(200), "TRY", date(2026, 10, 1))]
    assert budget_amount(daily, date(2026, 10, 1), date(2026, 10, 31)) == D("6200.00")
    assert budget_amount(daily, date(2026, 10, 25), date(2026, 10, 25)) == D("200.00")
    weekly = [Budget("Expenses:Food", "weekly", D(1400), "TRY", date(2026, 10, 1))]
    assert budget_amount(weekly, date(2026, 10, 1), date(2026, 10, 31)) == D("6200.00")
    monthly = [Budget("Expenses:Food", "monthly", D(3100), "TRY", date(2026, 10, 1))]
    assert budget_amount(monthly, date(2026, 10, 1), date(2026, 10, 10)) == D("1000.00")
    yearly = [Budget("Expenses:Travel", "yearly", D(36500), "TRY", date(2026, 1, 1))]
    assert budget_amount(yearly, date(2026, 10, 1), date(2026, 10, 31)) == D("3100.00")
    changed = [
        Budget("Expenses:Food", "daily", D(100), "TRY", date(2026, 10, 1)),
        Budget("Expenses:Food", "daily", D(200), "TRY", date(2026, 10, 16)),
    ]
    assert budget_amount(changed, date(2026, 10, 1), date(2026, 10, 31)) == D("4700.00")
    assert budget_amount(daily, date(2026, 9, 1), date(2026, 9, 30)) is None


def test_budget_report_counts_subcategories(books_dir: Path, tmp_path: Path) -> None:
    (books_dir / "budgets.beancount").write_text(
        '2026-10-01 custom "budget" Expenses:Food "monthly" 2000.00 TRY\n'
        '2026-10-01 custom "budget" Expenses:Fees "daily" 1.00 TRY\n',
        encoding="utf-8",
    )
    _all(books_dir, tmp_path)
    lines, notes = budget_report(load(books_dir), date(2026, 10, 1), date(2026, 10, 31), rates)
    by = {x.account: x for x in lines}
    assert by["Expenses:Food"].actual == D("1700.50")  # groceries + delivery
    assert by["Expenses:Food"].status == "warning"  # 85%
    assert by["Expenses:Fees"].budget == D("31.00")
    assert by["Expenses:Fees"].status == "ok"
    assert notes == []


def test_review_quick_add_and_recategorize(books_dir: Path, tmp_path: Path) -> None:
    _import(books_dir, "akbank", tmp_path / "akbank.csv")
    books = load(books_dir)
    queue = [t for t in books.transactions() if needs_review(t)]
    assert [t.narration for t in queue] == ["XYZ LTD STI"]
    fixed = recategorize(queue[0], books, "home", payee="XYZ Mobilya")
    books = write(books, replace={queue[0].meta["import_id"]: fixed}, message="review")
    assert not any(needs_review(t) for t in books.transactions())
    t = next(t for t in books.transactions() if t.narration == "XYZ LTD STI")
    assert t.postings[1].account == "Expenses:Shopping:Home"
    assert t.payee == "XYZ Mobilya"

    cash = quick_add(
        books, day=date(2026, 10, 12), amount=D("85"), category="coffee", payee="Kahveci"
    )
    books = write(books, [cash], message="add")
    added = next(t for t in books.transactions() if t.payee == "Kahveci")
    assert [(p.account, units(p)[0]) for p in added.postings] == [
        ("Assets:Cash:Wallet", D(-85)),
        ("Expenses:Food:Coffee", D(85)),
    ]
    salary = quick_add(
        books, day=date(2026, 10, 15), amount=D("1000"), category="bonus", payee="ACME",
        paid_from="akbank",
    )  # fmt: skip
    assert units(salary.postings[0])[0] == D(1000)
    with pytest.raises(QPValidationError, match="ambiguous"):
        quick_add(books, day=date(2026, 10, 12), amount=D(1), category="other", payee="x")


def test_a_change_that_breaks_the_books_is_rolled_back(books_dir: Path, tmp_path: Path) -> None:
    _import(books_dir, "akbank", tmp_path / "akbank.csv")
    books = load(books_dir)
    t = books.transactions()[-1]
    bad = t._replace(postings=[t.postings[0]._replace(account="Expenses:Nope"), t.postings[1]])
    before = {p: p.read_text() for p in (books_dir / "journal").glob("*.beancount")}
    with pytest.raises(BooksError):
        write(books, replace={t.meta["import_id"]: bad}, message="bad")
    assert {p: p.read_text() for p in (books_dir / "journal").glob("*.beancount")} == before


def test_unconfigured_source_and_changed_header(books_dir: Path, tmp_path: Path) -> None:
    sources = load_sources(books_dir / "sources.yaml")
    with pytest.raises(QPValidationError, match="no column mapping"):
        statement_rows(tmp_path / "akbank.csv", sources["empty"])
    (tmp_path / "changed.csv").write_text("Tarih;Açıklama;Miktar\n02.10.2026;x;1\n")
    with pytest.raises(QPValidationError, match="Tutar"):
        statement_rows(tmp_path / "changed.csv", sources["akbank"])


def test_parsers_and_fold() -> None:
    assert parse_amount("1.234,56", ",") == D("1234.56")
    assert parse_amount("-1.234,56 TL", ",") == D("-1234.56")
    assert parse_amount("1.234,56-", ",") == D("-1234.56")
    assert parse_amount("(12,00)", ",") == D("-12.00")
    assert parse_amount("$1,234.56", ".") == D("1234.56")
    assert parse_amount(D("12.5"), ",") == D("12.5")  # an Excel number is not re-parsed
    assert parse_amount("", ",") is None
    assert parse_date("05.10.2026 14:02", "%d.%m.%Y %H:%M") == date(2026, 10, 5)
    assert fold("MİGROS Şok ÇAĞDAŞ ılık") == "migros sok cagdas ilik"
    assert fold("Interest") == "interest"
