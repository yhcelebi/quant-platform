"""Alert rules (ADR 0010, 17): pure functions from performance and holdings to candidates.

Each candidate has a key ``(rule, subject, key_date)``; the store keeps one event per key,
so a condition that persists alerts once (a drawdown once per episode, keyed by its peak).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from qp.core import istanbul_date, to_float64, to_float64_list
from qp.news.store import NewsItem
from qp.performance.metrics import Summary
from qp.portfolio.holdings import HoldingsReport
from qp.stats import historical_var

PORTFOLIO = "portfolio"


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    rule: str
    subject: str
    key_date: date
    level: str  # info | warning | critical
    message: str


@dataclass(frozen=True, slots=True)
class Thresholds:
    daily_loss: Decimal
    drawdown: Decimal
    var_level: float
    stale_price_days: int
    var_lookback: int = 250
    var_min_observations: int = 60


def _pct(x: Decimal | float) -> str:
    return f"{float(x) * 100:.2f}%"


def performance_alerts(summary: Summary, t: Thresholds) -> list[AlertCandidate]:
    """Daily loss, VaR breach and drawdown on the latest valuation day of ``summary``."""
    out: list[AlertCandidate] = []
    if not summary.returns:
        return out
    day, r = summary.returns[-1]
    value = f"value {summary.end_value:,.2f} {summary.view.value.upper()}"
    if r < -t.daily_loss:
        out.append(
            AlertCandidate(
                "daily_loss",
                PORTFOLIO,
                day,
                "warning",
                f"Portfolio {_pct(r)} on {day} (limit -{_pct(t.daily_loss)}); {value}",
            )
        )
    history = [x for _, x in summary.returns[:-1]][-t.var_lookback :]
    if len(history) >= t.var_min_observations:
        var = historical_var([-x for x in to_float64_list(history)], t.var_level)
        if -to_float64(r) > var:
            out.append(
                AlertCandidate(
                    "var_breach",
                    PORTFOLIO,
                    day,
                    "critical",
                    f"Loss {_pct(r)} on {day} exceeds the {t.var_level:.0%} one-day VaR "
                    f"({_pct(-var)}, {len(history)} days); {value}",
                )
            )
    dd = summary.current_drawdown
    if dd < -t.drawdown:
        peak_w = max(w for _, w in summary.wealth)
        peak_day = max(d for d, w in summary.wealth if w == peak_w)
        out.append(
            AlertCandidate(
                "drawdown",
                PORTFOLIO,
                peak_day,
                "warning",
                f"Drawdown {_pct(dd)} since the peak on {peak_day} "
                f"(limit -{_pct(t.drawdown)}); {value}",
            )
        )
    return out


def data_alerts(report: HoldingsReport, t: Thresholds) -> list[AlertCandidate]:
    """Holdings without a price, or with a price older than ``stale_price_days``."""
    out: list[AlertCandidate] = []
    for row in report.positions:
        name = str(row.valuation.instrument)
        if row.price is None:
            out.append(
                AlertCandidate(
                    "missing_price",
                    name,
                    report.as_of,
                    "warning",
                    f"{name} ({row.valuation.account}) has no price on or before {report.as_of}",
                )
            )
        elif (report.as_of - row.price.valid_date).days > t.stale_price_days:
            out.append(
                AlertCandidate(
                    "stale_price",
                    name,
                    row.price.valid_date,
                    "warning",
                    f"{name} price is from {row.price.valid_date} "
                    f"({(report.as_of - row.price.valid_date).days} days old on {report.as_of})",
                )
            )
    return out


def news_alerts(items: list[NewsItem], held: set[str]) -> list[AlertCandidate]:
    """One alert per (news item, held instrument) the item is linked to (ADR 0012, 12)."""
    out: list[AlertCandidate] = []
    for item in items:
        for e in item.entities:
            if e.kind == "instrument" and e.key in held:
                out.append(
                    AlertCandidate(
                        "holding_news",
                        f"{e.key} #{item.id}",
                        istanbul_date(item.published_at),
                        "info",
                        f"{e.key} in the news ({item.source}, matched '{e.matched}'): "
                        f"{item.title} {item.url}",
                    )
                )
    return out
