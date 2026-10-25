"""JSON API for the household books web app (ADR 0017, 8). Thin: every number comes from
``qp.books``; amounts travel as strings so no float rounding happens on the way.

Run: ``make app`` (serves the built web app too). Localhost by default; any other bind
address requires ``QP_API__TOKEN`` and every request must carry ``Authorization: Bearer``.
"""

from __future__ import annotations

import argparse
import secrets
import tempfile
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any

from beancount.core import data
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from qp.books import service
from qp.books.config import add_rule, load_sources, rule_pattern
from qp.books.importer import needs_review, quick_add
from qp.books.ledger import Books, commit, load, units, write
from qp.books.periods import PERIODS, window
from qp.books.statements import (
    Rates,
    balance_sheet,
    budget_report,
    cash_flow,
    income_statement,
)
from qp.core import SystemClock, istanbul_date
from qp.core.errors import QPError
from qp.platform import Settings, init_platform

WEB = Path(__file__).resolve().parents[1] / "web" / "out"
Period = Annotated[str, Query(pattern="^(" + "|".join(PERIODS) + ")$")]


def s(x: Decimal | None) -> str | None:
    return None if x is None else f"{x:.2f}"


def tx_json(t: data.Transaction) -> dict[str, Any]:
    main = t.postings[0]
    n, c = units(main)
    category = next(
        (p.account for p in t.postings[1:] if p.account.startswith(("Expenses:", "Income:"))),
        t.postings[1].account if len(t.postings) > 1 else "",
    )
    return {
        "id": t.meta.get("import_id"),
        "date": str(t.date),
        "payee": t.payee,
        "description": t.narration,
        "account": main.account,
        "amount": s(n),
        "currency": c,
        "category": category,
        "tags": sorted(t.tags),
        "review": needs_review(t),
        "source": t.meta.get("source", "manual"),
    }


class Context:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._rates: Rates | None = None

    def books(self) -> Books:
        try:
            b = load(service.root(self.settings))
        except QPError as exc:
            raise HTTPException(404, str(exc)) from None
        return b

    def rates(self) -> Rates:
        if self._rates is None:
            self._rates = service.tcmb_rates(self.settings)
        return self._rates

    def today(self) -> date:
        return istanbul_date(SystemClock().now())


class NewTx(BaseModel):
    amount: str
    category: str
    payee: str = ""
    account: str = "Assets:Cash:Wallet"
    date: str | None = None
    currency: str = "TRY"
    note: str = ""
    tags: list[str] = []


class Categorize(BaseModel):
    category: str
    payee: str | None = None
    learn: bool = False


class SetBudget(BaseModel):
    account: str
    period: str
    amount: str
    start: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or init_platform()
    ctx = Context(cfg)
    token = cfg.api.token.get_secret_value() if cfg.api.token else ""
    app = FastAPI(title="qp books", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def auth(authorization: Annotated[str | None, Header()] = None) -> None:
        if token and not secrets.compare_digest(authorization or "", f"Bearer {token}"):
            raise HTTPException(401, "missing or wrong token")

    guarded = [Depends(auth)]

    def span(period: str, day: str | None, start: str | None, end: str | None) -> tuple[date, date]:
        try:
            if start and end:
                return date.fromisoformat(start), date.fromisoformat(end)
            return window(period, date.fromisoformat(day) if day else ctx.today())
        except (QPError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from None

    def books_call[T](fn: Callable[[], T]) -> T:
        try:
            return fn()
        except QPError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/meta", dependencies=guarded)
    def meta() -> dict[str, Any]:
        b = ctx.books()
        sources = (
            load_sources(b.root / "sources.yaml") if (b.root / "sources.yaml").exists() else {}
        )
        return {
            "today": str(ctx.today()),
            "categories": service.categories(b),
            "accounts": service.money_accounts(b),
            "sources": [
                {
                    "name": x.name,
                    "account": x.account,
                    "ready": x.configured,
                    "verified": x.verified,
                }
                for x in sources.values()
            ],
            "errors": b.errors,
            "periods": list(PERIODS),
        }

    @app.get("/api/dashboard", dependencies=guarded)
    def dashboard(period: Period = "month", day: str | None = None) -> dict[str, Any]:
        b = ctx.books()
        start, end = span(period, day, None, None)
        stmt = income_statement(b.transactions(), start, end, ctx.rates())
        lines, notes = budget_report(b, start, end, ctx.rates())
        recent = service.transactions_between(b, date.min, date.max)[:8]
        return {
            "start": str(start),
            "end": str(end),
            "income": s(stmt.total_income),
            "expenses": s(stmt.total_expenses),
            "net": s(stmt.net),
            "savings_rate": None if stmt.savings_rate is None else f"{stmt.savings_rate:.4f}",
            "spending": [{"group": k, "amount": s(v)} for k, v in stmt.groups("expenses").items()],
            "budgets": [
                {
                    "account": x.account,
                    "period": x.period,
                    "budget": s(x.budget),
                    "actual": s(x.actual),
                    "remaining": s(x.remaining),
                    "used": None if x.used is None else f"{x.used:.4f}",
                    "status": x.status,
                }
                for x in lines
            ],
            "review": sum(1 for t in b.transactions() if needs_review(t)),
            "recent": [tx_json(t) for t in recent],
            "notes": notes + stmt.missing_rates,
        }

    @app.get("/api/transactions", dependencies=guarded)
    def transactions(
        period: Period = "month",
        day: str | None = None,
        start: str | None = None,
        end: str | None = None,
        q: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        a, z = span(period, day, start, end)
        txs = service.transactions_between(ctx.books(), a, z, text=q, account=account)
        return {"start": str(a), "end": str(z), "items": [tx_json(t) for t in txs]}

    @app.post("/api/transactions", dependencies=guarded)
    def add(body: NewTx) -> dict[str, Any]:
        try:
            amount = Decimal(body.amount.replace(",", "."))
        except InvalidOperation:
            raise HTTPException(422, f"not an amount: {body.amount}") from None

        def run() -> data.Transaction:
            b = ctx.books()
            t = quick_add(
                b,
                day=date.fromisoformat(body.date) if body.date else ctx.today(),
                amount=amount,
                category=body.category,
                payee=body.payee,
                paid_from=body.account,
                currency=body.currency.upper(),
                note=body.note,
                tags=tuple(body.tags),
            )
            write(b, [t], message=f"app: add {amount} {body.category} {body.payee}")
            return t

        return tx_json(books_call(run))

    @app.get("/api/review", dependencies=guarded)
    def review() -> dict[str, Any]:
        return {"items": [tx_json(t) for t in service.review_queue(ctx.books())]}

    @app.post("/api/transactions/{import_id}/category", dependencies=guarded)
    def categorize(import_id: str, body: Categorize) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            t = service.set_category(cfg, import_id, body.category, payee=body.payee)
            learned = None
            if body.learn:
                pattern = rule_pattern(t.narration or t.payee or "")
                account = next(
                    p.account for p in t.postings if p.account.startswith(("Expenses:", "Income:"))
                )
                add_rule(service.root(cfg) / "rules.yaml", pattern, account, body.payee)
                commit(service.root(cfg), f"app: learn rule {pattern} -> {account}")
                learned = pattern
            return {**tx_json(t), "learned": learned}

        return books_call(run)

    @app.get("/api/budgets", dependencies=guarded)
    def budgets(period: Period = "month", day: str | None = None) -> dict[str, Any]:
        a, z = span(period, day, None, None)
        lines, notes = budget_report(ctx.books(), a, z, ctx.rates())
        return {
            "start": str(a),
            "end": str(z),
            "items": [
                {
                    "account": x.account,
                    "period": x.period,
                    "budget": s(x.budget),
                    "actual": s(x.actual),
                    "remaining": s(x.remaining),
                    "used": None if x.used is None else f"{x.used:.4f}",
                    "status": x.status,
                }
                for x in lines
            ],
            "notes": notes,
        }

    @app.post("/api/budgets", dependencies=guarded)
    def set_budget(body: SetBudget) -> dict[str, str]:
        try:
            amount = Decimal(body.amount.replace(",", "."))
        except InvalidOperation:
            raise HTTPException(422, f"not an amount: {body.amount}") from None
        start = date.fromisoformat(body.start) if body.start else ctx.today()
        books_call(lambda: service.set_budget(cfg, body.account, body.period, amount, start=start))
        return {"ok": "saved"}

    @app.get("/api/statements/income", dependencies=guarded)
    def income(
        period: Period = "month",
        day: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        a, z = span(period, day, start, end)
        st = income_statement(ctx.books().transactions(), a, z, ctx.rates())
        return {
            "start": str(a),
            "end": str(z),
            "income": [{"account": k, "amount": s(v)} for k, v in st.income.items()],
            "expenses": [{"account": k, "amount": s(v)} for k, v in st.expenses.items()],
            "total_income": s(st.total_income),
            "total_expenses": s(st.total_expenses),
            "net": s(st.net),
            "savings_rate": None if st.savings_rate is None else f"{st.savings_rate:.4f}",
            "missing_rates": st.missing_rates,
        }

    @app.get("/api/statements/balance", dependencies=guarded)
    def balance(day: str | None = None) -> dict[str, Any]:
        d = date.fromisoformat(day) if day else ctx.today()
        b = balance_sheet(ctx.books().transactions(), d, ctx.rates())

        def lines(xs: list[Any]) -> list[dict[str, Any]]:
            return [
                {
                    "account": x.account,
                    "native": {c: s(v) for c, v in x.native.items()},
                    "value": s(x.value),
                }
                for x in xs
            ]

        return {
            "date": str(d),
            "assets": lines(b.assets),
            "liabilities": lines(b.liabilities),
            "total_assets": s(b.total_assets),
            "total_liabilities": s(b.total_liabilities),
            "net_worth": s(b.net_worth),
            "opening_equity": s(b.opening_equity),
            "retained": s(b.retained),
            "revaluation": s(b.revaluation),
            "missing_rates": b.missing_rates,
        }

    @app.get("/api/statements/cashflow", dependencies=guarded)
    def cashflow(
        period: Period = "month",
        day: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        a, z = span(period, day, start, end)
        c = cash_flow(ctx.books().transactions(), a, z, ctx.rates())
        return {
            "start": str(a),
            "end": str(z),
            "opening_cash": s(c.opening_cash),
            "sections": [
                {
                    "section": sec,
                    "total": s(c.section(sec)),
                    "lines": [{"group": g, "amount": s(v)} for g, v in lines.items()],
                }
                for sec, lines in c.lines.items()
            ],
            "net_flow": s(c.net_flow),
            "revaluation": s(c.revaluation),
            "closing_cash": s(c.closing_cash),
            "missing_rates": c.missing_rates,
        }

    @app.post("/api/import", dependencies=guarded)
    def import_statement(
        file: Annotated[UploadFile, File()],
        source: Annotated[str, Form()],
        dry_run: Annotated[bool, Form()] = True,
    ) -> dict[str, Any]:
        suffix = Path(file.filename or "statement.csv").suffix.lower()
        if suffix not in (".csv", ".txt", ".xlsx", ".xlsm", ".xls"):
            raise HTTPException(422, f"unsupported file type {suffix}")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"statement{suffix}"
            path.write_bytes(file.file.read())
            r = books_call(lambda: service.import_file(cfg, path, source, dry_run=dry_run))
        p = r.plan
        return {
            "written": r.written,
            "new": [tx_json(t) for t in p.new],
            "categorized": p.categorized,
            "uncategorized": p.uncategorized,
            "duplicates": p.duplicates,
            "transfers": p.transfers,
            "skipped": p.skipped,
        }

    if WEB.is_dir():  # the built web app (apps/web: `pnpm build`)
        app.mount("/_next", StaticFiles(directory=WEB / "_next"), name="next")

        @app.get("/{path:path}", include_in_schema=False)
        def web(path: str) -> FileResponse:
            for candidate in (WEB / path, WEB / f"{path}.html", WEB / path / "index.html"):
                if candidate.is_file() and WEB in candidate.resolve().parents:
                    return FileResponse(candidate)
            return FileResponse(WEB / "index.html")

    return app


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="qp books web app and API")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    cfg = init_platform()
    host = args.host or cfg.api.host
    if host not in ("127.0.0.1", "localhost", "::1") and cfg.api.token is None:
        print("refusing to listen beyond localhost without QP_API__TOKEN in .env")
        return 2
    uvicorn.run(create_app(cfg), host=host, port=args.port or cfg.api.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
