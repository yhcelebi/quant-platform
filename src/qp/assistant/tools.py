"""Read-only tools the assistant may call (ADR 0016, 11). Each one wraps a ``qp`` function
and returns JSON-ready data with the moment it describes; none of them writes, fetches,
trades, deploys or promotes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from qp.core import Clock, SystemClock, istanbul_date
from qp.decisions.journal import entries, theses
from qp.macro.registry import indicator_history, load_registry
from qp.macro.transforms import Transform
from qp.news.store import query as news_query
from qp.optimization.methods import Method
from qp.optimization.service import optimize
from qp.performance.benchmarks import BenchmarkResult
from qp.performance.metrics import Summary, View
from qp.performance.service import (
    Context,
    default_start,
    load_context,
    performance_report,
    series_data,
)
from qp.platform.db import Database
from qp.platform.results import list_runs, read_summary
from qp.platform.settings import Settings, default_config_dir
from qp.portfolio.holdings import build_report
from qp.risk import gate
from qp.risk.service import asset_classes, risk_report
from qp.scenarios.service import scenario_report
from qp.strategies.promotion import deployments

type Json = dict[str, Any]


def _day(args: Json, key: str, default: date) -> date:
    raw = args.get(key)
    return date.fromisoformat(raw) if raw else default


def _num(x: Decimal | float | None, places: int = 2) -> str | None:
    """Exact Decimal values as strings (no float drift); floats rounded for reading."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return str(x)
    if x != x:  # nan
        return None
    return f"{x:.{places}f}"


def _pct(x: Decimal | float | None) -> str | None:
    """A fraction shown as a percentage string with two decimals."""
    if x is None or x != x:
        return None
    return f"{float(x) * 100:.2f}%"


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    run: Callable[[Json], Json]

    def schema(self) -> Json:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


DAY = {"type": "string", "description": "Day YYYY-MM-DD; omit for today"}
ACCOUNTS = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Account codes to include; omit for all",
}


@dataclass
class Toolbox:
    """Lazily loads the ledger and market data once; every tool reads, none writes."""

    settings: Settings
    clock: Clock = field(default_factory=SystemClock)
    context: Context | None = None
    _db: Database | None = None

    @property
    def today(self) -> date:
        return istanbul_date(self.clock.now())

    def ctx(self) -> Context:
        if self.context is None:
            self.context = load_context(self.settings)
        return self.context

    def db(self) -> Database:
        if self._db is None:
            self._db = Database.from_settings(self.settings.db)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.dispose()
            self._db = None

    # ------------------------------------------------------------------ tools

    def holdings(self, args: Json) -> Json:
        day = _day(args, "day", self.today)
        ctx = self.ctx()
        r = build_report(
            ctx.transactions,
            ctx.market,
            as_of=day,
            accounts=args.get("accounts"),
            stale_after_days=self.settings.performance.stale_after_days,
        )
        return {
            "day": str(day),
            "positions": [
                {
                    "account": p.valuation.account,
                    "instrument": str(p.valuation.instrument),
                    "currency": p.valuation.currency.code,
                    "quantity": _num(p.valuation.quantity),
                    "price": _num(p.valuation.price),
                    "price_date": str(p.price.valid_date) if p.price else None,
                    "value_try": _num(p.valuation.value_try),
                    "cost_try": _num(p.valuation.cost_try),
                    "unrealized_try": _num(p.valuation.unrealized_try),
                    "stale": p.stale,
                }
                for p in r.positions
            ],
            "cash": [
                {
                    "account": c.account,
                    "currency": c.currency.code,
                    "amount": _num(c.amount),
                    "amount_try": _num(c.amount_try),
                }
                for c in r.cash
            ],
            "total_try": _num(r.total_try),
            "complete": r.complete,
            "warnings": list(r.warnings),
        }

    def performance(self, args: Json) -> Json:
        end = _day(args, "end", self.today)
        start = _day(args, "start", default_start(end))
        view = View(args.get("view", "TRY"))
        rep = performance_report(
            self.ctx(),
            end=end,
            start=start,
            view=view,
            accounts=args.get("accounts"),
            with_periods=False,
        )
        s = rep.summary
        if not isinstance(s, Summary):
            return {
                "start": str(start),
                "end": str(end),
                "view": view.value,
                "unavailable": s.reason,
            }
        out: Json = {
            "view": view.value,
            "start": str(s.start),
            "end": str(s.end),
            "start_value": _num(s.start_value),
            "end_value": _num(s.end_value),
            "net_flows": _num(s.net_flows),
            "pnl": _num(s.pnl),
            "twr": _pct(s.twr),
            "twr_annualized": _pct(s.twr_annualized),
            "mwr_annualized": _pct(s.xirr),
            "volatility_annualized": _pct(s.volatility),
            "max_drawdown": _pct(s.max_drawdown.depth) if s.max_drawdown else None,
            "current_drawdown": _pct(s.current_drawdown),
            "notes": list(s.notes),
            "stale": list(s.stale),
        }
        out["benchmarks"] = [
            {"name": spec.name, "return": _pct(b.total)}
            if isinstance(b, BenchmarkResult)
            else {"name": spec.name, "unavailable": b.reason}
            for spec, b in rep.benchmarks
        ]
        return out

    def risk(self, args: Json) -> Json:
        day = _day(args, "day", self.today)
        ctx = self.ctx()
        r = risk_report(ctx, day=day, accounts=args.get("accounts"), classes=asset_classes(ctx))
        var = []
        for v in (r.portfolio, r.simulation):
            if v is None:
                continue
            var.append(
                {
                    "source": v.source,
                    "value_try": _num(v.value),
                    "days": v.observations,
                    "unavailable": v.unavailable,
                    "figures": [
                        {
                            "method": f.method.value,
                            "level": _pct(f.level),
                            "var_try": _num(f.var),
                            "es_try": _num(f.es),
                        }
                        for f in v.figures
                    ],
                    "left_out": v.excluded,
                }
            )
        e = r.exposure
        return {
            "day": str(day),
            "var_1day": var,
            "volatility_annualized": _pct(r.volatility),
            "by_currency": [{"name": x.name, "weight": _pct(x.weight)} for x in e.by_currency],
            "by_asset_class": [
                {"name": x.name, "weight": _pct(x.weight)} for x in e.by_asset_class
            ],
            "top_holdings": [
                {"name": x.name, "value_try": _num(x.value_try), "weight": _pct(x.weight)}
                for x in e.by_holding[:10]
            ],
            "herfindahl": _num(e.herfindahl, 4),
            "notes": r.notes,
        }

    def scenarios(self, args: Json) -> Json:
        day = _day(args, "day", self.today)
        ctx = self.ctx()
        rep = scenario_report(
            ctx, day=day, classes=asset_classes(ctx), names=args.get("names") or None
        )
        return {
            "day": str(day),
            "value_try": _num(rep.value_try),
            "scenarios": [
                {
                    "name": r.name,
                    "kind": r.kind,
                    "description": r.description,
                    "window": [str(r.window[0]), str(r.window[1])] if r.window else None,
                    "pnl_try": _num(r.pnl),
                    "change_of_covered": _pct(r.change),
                    "covered_try": _num(r.covered_try),
                    "missing": [m.name for m in r.missing],
                    "unavailable": r.unavailable,
                    "notes": r.notes,
                }
                for r in rep.results
            ],
            "notes": rep.notes,
        }

    def optimize(self, args: Json) -> Json:
        day = _day(args, "day", self.today)
        r = optimize(
            self.ctx(),
            day=day,
            method=Method(args["method"]),
            extra=args.get("extra") or (),
        )
        return {
            "day": str(day),
            "method": r.method.value,
            "unavailable": r.unavailable,
            "days_used": r.observations,
            "first_day": str(r.first_day) if r.first_day else None,
            "invested_try": _num(r.invested_try),
            "volatility_annualized": _pct(r.volatility),
            "current_volatility_annualized": _pct(r.current_volatility),
            "cvar_daily": _pct(r.cvar),
            "current_cvar_daily": _pct(r.current_cvar),
            "assets": [
                {
                    "name": a.name,
                    "current_weight": _pct(a.current),
                    "model_weight": _pct(a.target),
                    "risk_share": _pct(a.risk_share),
                    "model_trade_try": _num(a.trade_try),
                }
                for a in r.assets
            ],
            "excluded": r.excluded,
            "notes": r.notes,
            "disclaimer": "model output; no order is placed",
        }

    def macro(self, args: Json) -> Json:
        reg = load_registry(default_config_dir() / "indicators.yaml")
        if not args.get("indicator"):
            return {
                "indicators": [
                    {"id": i.id, "name": i.name, "unit": i.unit, "country": i.country}
                    for i in reg.indicators
                ]
            }
        ind = reg.get(args["indicator"])
        transform = Transform(args.get("transform", "level"))
        now = self.clock.now()
        obs, raw = indicator_history(
            ind,
            series_data(self.settings),
            until=self.today,
            as_of=now,
            transform=transform,
        )
        last = int(args.get("last", 12))
        return {
            "indicator": ind.id,
            "name": ind.name,
            "unit": ind.unit,
            "transform": transform.value,
            "known_at": now.isoformat(),
            "observations": [{"date": str(d), "value": _num(v, 4)} for d, v in obs[-last:]],
            "source": raw[-1].source if raw else None,
        }

    def news(self, args: Json) -> Json:
        now = self.clock.now()
        days = int(args.get("days", 14))
        items = news_query(
            self.db(),
            as_of=now,
            since=now - timedelta(days=days),
            tag_filter=args.get("tag"),
            entity_keys=args.get("entities"),
            limit=int(args.get("limit", 20)),
        )
        text = (args.get("text") or "").casefold()
        return {
            "known_at": now.isoformat(),
            "items": [
                {
                    "published": i.published_at.isoformat(),
                    "source": i.source,
                    "title": i.title,
                    "url": i.url,
                    "tags": list(i.tags),
                    "entities": [e.key for e in i.entities],
                }
                for i in items
                if not text or text in i.title.casefold()
            ],
        }

    def journal(self, args: Json) -> Json:
        db = self.db()
        return {
            "theses": [
                {
                    "id": t.id,
                    "title": t.title,
                    "instrument": t.instrument,
                    "direction": t.direction,
                    "status": t.status,
                    "next_review": str(t.next_review) if t.next_review else None,
                    "thesis": t.thesis,
                    "invalidation": t.invalidation,
                    "last_note": t.last_note,
                }
                for t in theses(db)
            ],
            "recent_entries": [
                {"created": e.created_at.isoformat(), "kind": e.kind, "text": e.text}
                for e in entries(db, limit=int(args.get("limit", 10)))
            ],
        }

    def backtests(self, args: Json) -> Json:
        runs = []
        for folder in list_runs(self.settings)[-int(args.get("limit", 10)) :]:
            s = read_summary(folder)
            runs.append(
                {
                    "run": folder.name,
                    "superseded": (folder / "SUPERSEDED.txt").exists(),
                    "summary": s,
                }
            )
        return {"runs": runs}

    def paper(self, args: Json) -> Json:
        db = self.db()
        return {
            "global_halt": gate.halted(db, "global"),
            "strategies": [
                {
                    "name": d.name,
                    "signal": d.signal,
                    "instrument": d.instrument or f"FX {d.fx}",
                    "status": d.status,
                    "halted": gate.halted(db, d.name),
                    "capital": _num(d.capital),
                }
                for d in deployments(db)
            ],
        }

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "holdings",
                "Positions and cash with prices, TRY values and unrealized P&L on a day.",
                {"day": DAY, "accounts": ACCOUNTS},
                (),
                self.holdings,
            ),
            Tool(
                "performance",
                "Performance summary (TWR, MWR, P&L, volatility, drawdown, benchmarks) "
                "between two days in TRY, USD or REAL (inflation-adjusted TRY) terms.",
                {
                    "start": {"type": "string", "description": "YYYY-MM-DD; omit for 1 year"},
                    "end": DAY,
                    "view": {"type": "string", "enum": ["TRY", "USD", "REAL"]},
                    "accounts": ACCOUNTS,
                },
                (),
                self.performance,
            ),
            Tool(
                "risk",
                "1-day VaR/ES, realized volatility, exposures by currency and asset class, "
                "top holdings and concentration on a day.",
                {"day": DAY, "accounts": ACCOUNTS},
                (),
                self.risk,
            ),
            Tool(
                "scenarios",
                "Stress scenarios on today's holdings: shocks from configs/scenarios.yaml, "
                "historical replays and the worst observed 1/5/20-day losses.",
                {
                    "day": DAY,
                    "names": {"type": "array", "items": {"type": "string"}},
                },
                (),
                self.scenarios,
            ),
            Tool(
                "optimize",
                "Model weights for today's holdings (TRY cash excluded) with a weight "
                "model, compared with current weights. A model output; nothing is traded.",
                {
                    "method": {"type": "string", "enum": [m.value for m in Method]},
                    "day": DAY,
                    "extra": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Assets not held to include: currency codes (USD) "
                        "or SYMBOL.VENUE",
                    },
                },
                ("method",),
                self.optimize,
            ),
            Tool(
                "macro",
                "Macro indicators: without an indicator id, the list; with one, its latest "
                "observations as known now.",
                {
                    "indicator": {"type": "string"},
                    "transform": {"type": "string", "enum": [t.value for t in Transform]},
                    "last": {"type": "integer"},
                },
                (),
                self.macro,
            ),
            Tool(
                "news",
                "Recent headlines from official institutions (TCMB, Fed, ECB), titles and "
                "links only, optionally filtered by tag, entity key or a word in the title.",
                {
                    "days": {"type": "integer"},
                    "tag": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                (),
                self.news,
            ),
            Tool(
                "journal",
                "Investment theses with their status and review dates, and recent journal entries.",
                {"limit": {"type": "integer"}},
                (),
                self.journal,
            ),
            Tool(
                "backtests",
                "Stored backtest and walk-forward runs with their summaries.",
                {"limit": {"type": "integer"}},
                (),
                self.backtests,
            ),
            Tool(
                "paper",
                "Deployed paper strategies, their promotion status and kill-switch state.",
                {},
                (),
                self.paper,
            ),
        ]
