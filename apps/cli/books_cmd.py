"""``qp books``: household books - import statements, add spending, review, budgets and the
three statements (ADR 0017)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, money, pct, settings
from qp.books import ledger as books_ledger
from qp.books import service
from qp.books.config import add_rule, load_rules, load_sources, rule_pattern
from qp.books.importer import quick_add
from qp.books.periods import PERIODS, month, window
from qp.books.reader import preview
from qp.books.statements import balance_sheet, budget_report, cash_flow, income_statement
from qp.core import SystemClock, istanbul_date
from qp.core.errors import QPError

app = typer.Typer(no_args_is_help=True, help="Household books: spending, budgets, statements.")

Day = Annotated[str | None, typer.Option("--date", help="YYYY-MM-DD (default: today)")]


def _today() -> date:
    return istanbul_date(SystemClock().now())


def _books() -> books_ledger.Books:
    try:
        b = books_ledger.load(service.root(settings()))
    except QPError as exc:
        fail(str(exc))
    for e in b.errors:
        console.print(f"[red]books error:[/red] {e}")
    return b


def _range(month_: str | None, period: str | None, day: str | None) -> tuple[date, date]:
    d = date.fromisoformat(day) if day else _today()
    try:
        if month_:
            return month(month_)
        return window(period or "month", d)
    except QPError as exc:
        fail(str(exc))


@app.command("init")
def init() -> None:
    """Create data/books/ from the templates (existing files are kept)."""
    cfg = settings()
    created = books_ledger.init(service.root(cfg), service.templates())
    console.print(f"books at {service.root(cfg)}; created: {', '.join(created) or 'nothing new'}")
    console.print("Next: opening balances in manual.beancount, budgets in budgets.beancount,")
    console.print("then `uv run qp books inspect <statement file>` for each bank.")


@app.command("inspect")
def inspect(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    sheet: Annotated[str | None, typer.Option(help="Excel sheet name")] = None,
) -> None:
    """Show the first rows of a statement, to fill its column mapping in sources.yaml."""
    try:
        rows = preview(file, sheet)
    except QPError as exc:
        fail(str(exc))
    t = Table(*(f"col {i + 1}" for i in range(max((len(r) for r in rows), default=0))))
    for r in rows:
        t.add_row(*r)
    console.print(t)


@app.command("import")
def import_cmd(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    source: Annotated[str, typer.Option(help="Institution in sources.yaml, e.g. akbank")],
    dry_run: Annotated[bool, typer.Option(help="Show what would be added")] = False,
) -> None:
    """Import a bank statement: categorize by rules, skip duplicates and transfers."""
    try:
        r = service.import_file(settings(), file, source, dry_run=dry_run)
    except QPError as exc:
        fail(str(exc))
    p = r.plan
    t = Table("date", "amount", "category", "description", title=f"{source}: {len(p.new)} new")
    for x in p.new:
        own, counter = x.postings[0], x.postings[1]
        n, c = books_ledger.units(own)
        mark = "[yellow]?[/yellow] " if x.flag == "!" else ""
        t.add_row(str(x.date), f"{money(n)} {c}", mark + counter.account, x.narration)
    console.print(t)
    console.print(
        f"{p.categorized} categorized, {p.uncategorized} to review, {p.duplicates} already "
        f"imported, {len(p.transfers)} transfer(s) already recorded from the other side"
    )
    for s in p.skipped:
        console.print(f"[dim]skipped {s}[/dim]")
    console.print("[yellow]dry run: nothing written[/yellow]" if dry_run else "")


@app.command("add")
def add(
    amount: Annotated[str, typer.Argument(help="e.g. 120 or 120.50")],
    category: Annotated[str, typer.Argument(help="e.g. groceries, coffee, salary")],
    payee: Annotated[str, typer.Argument(help="Who you paid / who paid you")] = "",
    paid_from: Annotated[str, typer.Option("--from", help="Account: cash, akbank, papara")] = (
        "cash"
    ),
    day: Day = None,
    currency: Annotated[str, typer.Option(help="Currency")] = "TRY",
    note: Annotated[str, typer.Option(help="A note")] = "",
    tag: Annotated[list[str] | None, typer.Option(help="Tag, e.g. trip-izmir")] = None,
) -> None:
    """Record spending (or income) by hand: `qp books add 120 groceries Migros`."""
    try:
        value = Decimal(amount.replace(",", "."))
    except InvalidOperation:
        fail(f"not an amount: {amount}")
    b = _books()
    try:
        t = quick_add(
            b,
            day=date.fromisoformat(day) if day else _today(),
            amount=value,
            category=category,
            payee=payee,
            paid_from=paid_from,
            currency=currency.upper(),
            note=note,
            tags=tuple(tag or ()),
        )
        books_ledger.write(b, [t], message=f"add {value} {category} {payee}")
    except QPError as exc:
        fail(str(exc))
    console.print(
        f"added {t.date}: {money(value)} {currency.upper()} {t.postings[1].account} "
        f"from {t.postings[0].account}"
    )


@app.command("review")
def review() -> None:
    """Transactions waiting for a category."""
    queue = service.review_queue(_books())
    if not queue:
        console.print("nothing to review")
        return
    t = Table("date", "amount", "account", "description", title=f"{len(queue)} to review")
    t.add_column("id (for categorize)", no_wrap=True)
    for x in queue:
        n, c = books_ledger.units(x.postings[0])
        short = str(x.meta["import_id"]).split(":")
        t.add_row(str(x.date), f"{money(n)} {c}", x.postings[0].account, x.narration,
                  f"{short[0]}:{short[-1][:8]}")  # fmt: skip
    console.print(t)
    console.print("Categorize: uv run qp books categorize ID CATEGORY [--learn]")


@app.command("categorize")
def categorize(
    import_id: Annotated[str, typer.Argument(help="Id from `qp books review` (a prefix is fine)")],
    category: Annotated[str, typer.Argument(help="e.g. groceries, home, salary")],
    payee: Annotated[str | None, typer.Option(help="Clean payee name")] = None,
    learn: Annotated[bool, typer.Option(help="Also add a rule for next time")] = False,
) -> None:
    """Set a transaction's category; with --learn, similar lines are categorized next time."""
    cfg = settings()
    try:
        t = service.set_category(cfg, import_id, category, payee=payee)
        if learn:
            pattern = rule_pattern(t.narration or t.payee or "")
            account = next(
                p.account for p in t.postings if p.account.startswith(("Expenses:", "Income:"))
            )
            add_rule(service.root(cfg) / "rules.yaml", pattern, account, payee)
            books_ledger.commit(service.root(cfg), f"learn rule {pattern} -> {account}")
            console.print(f"learned: '{pattern}' -> {account}")
    except QPError as exc:
        fail(str(exc))
    console.print(f"{t.date} {t.narration}: {category}")


@app.command("rules-test")
def rules_test(
    text: Annotated[str, typer.Argument(help="A bank description")],
    amount: Annotated[float, typer.Option(help="Signed amount (negative = spending)")] = -1.0,
    source: Annotated[str, typer.Option(help="Institution")] = "",
) -> None:
    """Which rule a description matches."""
    rules = load_rules(service.root(settings()) / "rules.yaml")
    r = rules.match(source, text, Decimal(str(amount)))
    console.print(f"{r.account}  (rule: {r.text})" if r else "no rule matches: uncategorized")


@app.command("sources")
def sources() -> None:
    """Institutions and whether their statement format is set up."""
    s = load_sources(service.root(settings()) / "sources.yaml")
    t = Table("source", "account", "currency", "columns set", "verified")
    for x in s.values():
        t.add_row(x.name, x.account, x.currency, "yes" if x.configured else "no",
                  "yes" if x.verified else "no")  # fmt: skip
    console.print(t)


@app.command("income")
def income(
    month_: Annotated[str | None, typer.Option("--month", help="YYYY-MM")] = None,
    period: Annotated[str | None, typer.Option(help=" | ".join(PERIODS))] = None,
    day: Day = None,
    detail: Annotated[bool, typer.Option(help="Every category, not just groups")] = False,
) -> None:
    """Income statement: income, expenses by category, savings and savings rate (TRY)."""
    start, end = _range(month_, period, day)
    s = income_statement(_books().transactions(), start, end, service.tcmb_rates(settings()))
    t = Table("", "TRY", "share", title=f"Income statement {start} → {end}")
    for name, side, total in (("Income", "income", s.total_income),
                              ("Expenses", "expenses", s.total_expenses)):  # fmt: skip
        t.add_row(f"[bold]{name}[/bold]", f"[bold]{money(total)}[/bold]", "")
        items = (s.income if side == "income" else s.expenses) if detail else s.groups(side)
        for k, v in sorted(items.items(), key=lambda kv: -kv[1]):
            t.add_row("  " + k.split(":", 1)[1], money(v), pct(v / total) if total else "—")
    t.add_row("[bold]Net savings[/bold]", f"[bold]{money(s.net)}[/bold]", pct(s.savings_rate))
    console.print(t)
    for m in s.missing_rates:
        console.print(f"[yellow]no TCMB rate:[/yellow] {m} (left out)")


@app.command("balance")
def balance(
    as_of: Annotated[str | None, typer.Option(help="YYYY-MM-DD (default: today)")] = None,
) -> None:
    """Balance sheet: assets, liabilities and net worth in TRY."""
    day = date.fromisoformat(as_of) if as_of else _today()
    s = balance_sheet(_books().transactions(), day, service.tcmb_rates(settings()))
    t = Table("account", "balance", "TRY", title=f"Balance sheet {day}")
    for title, lines in (("Assets", s.assets), ("Liabilities", s.liabilities)):
        t.add_row(f"[bold]{title}[/bold]", "", "")
        for x in lines:
            native = ", ".join(f"{money(v)} {c}" for c, v in x.native.items())
            t.add_row("  " + x.account.split(":", 1)[1], native, money(x.value))
    t.add_row("[bold]Net worth[/bold]", "", f"[bold]{money(s.net_worth)}[/bold]")
    t.add_row("  opening balances", "", money(s.opening_equity))
    t.add_row("  savings to date", "", money(s.retained))
    t.add_row("  currency revaluation", "", money(s.revaluation))
    console.print(t)
    console.print("[dim]Brokers show net amounts transferred until their ledger is linked.[/dim]")
    for m in s.missing_rates:
        console.print(f"[yellow]no TCMB rate:[/yellow] {m}")


@app.command("cashflow")
def cashflow(
    month_: Annotated[str | None, typer.Option("--month", help="YYYY-MM")] = None,
    period: Annotated[str | None, typer.Option(help=" | ".join(PERIODS))] = None,
    day: Day = None,
) -> None:
    """Cash flow statement (direct method) for bank, wallet and cash accounts."""
    start, end = _range(month_, period, day)
    s = cash_flow(_books().transactions(), start, end, service.tcmb_rates(settings()))
    t = Table("", "TRY", title=f"Cash flow {start} → {end}")
    t.add_row("[bold]Opening cash[/bold]", money(s.opening_cash))
    names = {"opening": "Opening balances", "operating": "Operating (income and spending)",
             "investing": "Investing", "financing": "Financing (loans)",
             "exchange": "Currency exchange", "other": "Other"}  # fmt: skip
    for section, title in names.items():
        if section not in s.lines:
            continue
        t.add_row(f"[bold]{title}[/bold]", money(s.section(section)))
        for g, v in sorted(s.lines[section].items(), key=lambda kv: kv[1]):
            t.add_row("  " + g.split(":", 1)[1], money(v))
    t.add_row("Currency revaluation of cash", money(s.revaluation))
    t.add_row("[bold]Closing cash[/bold]", money(s.closing_cash))
    console.print(t)
    for m in s.missing_rates:
        console.print(f"[yellow]no TCMB rate:[/yellow] {m}")


@app.command("budget")
def budget(
    period: Annotated[str, typer.Option(help=" | ".join(PERIODS))] = "month",
    month_: Annotated[str | None, typer.Option("--month", help="YYYY-MM")] = None,
    day: Day = None,
) -> None:
    """Budget vs actual for today, this week, month, quarter or year."""
    start, end = _range(month_, period, day)
    cfg = settings()
    lines, notes = budget_report(_books(), start, end, service.tcmb_rates(cfg))
    if not lines:
        console.print("no budgets yet: set them in data/books/budgets.beancount")
    t = Table("category", "budget", "spent", "left", "used", "", title=f"Budget {start} → {end}")
    style = {"ok": "green", "warning": "yellow", "over": "red", "no budget": "dim"}
    for x in lines:
        t.add_row(x.account.split(":", 1)[1], money(x.budget), money(x.actual),
                  money(x.remaining), pct(x.used, 0),
                  f"[{style[x.status]}]{x.status}[/]")  # fmt: skip
    console.print(t)
    for n in notes:
        console.print(f"[yellow]note:[/yellow] {n}")


@app.command("check")
def check() -> None:
    """Load the books and report any error (unbalanced entries, unknown accounts...)."""
    b = _books()
    if b.errors:
        raise typer.Exit(code=1)
    console.print(f"ok: {len(b.transactions())} transactions, {len(b.accounts())} accounts")
