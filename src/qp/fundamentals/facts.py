"""Point-in-time fundamentals from SEC company facts (ADR 0013, 8-9).

Every query takes ``as_of``: only facts filed by then are visible, and a value restated in a
later filing replaces the earlier one only from that filing on.

TTM of a flow (income, cash flow) as of ``t``: ``FY + YTD_current - YTD_prior`` when a
quarterly report newer than the last annual one is known, else the last annual value.
Balance-sheet items (instants) take the latest value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from qp.core import ensure_utc
from qp.data.connectors import sec
from qp.data.lake import CleanStore

# Concepts in order of preference: companies use different tags for the same line.
REVENUE = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
)
NET_INCOME = ("NetIncomeLoss",)
OPERATING_INCOME = ("OperatingIncomeLoss",)
D_AND_A = ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization")
CFO = ("NetCashProvidedByUsedInOperatingActivities",)
CAPEX = ("PaymentsToAcquirePropertyPlantAndEquipment",)
EQUITY = ("StockholdersEquity",)
CASH = ("CashAndCashEquivalentsAtCarryingValue",)
DEBT_LONG = ("LongTermDebtNoncurrent", "LongTermDebt")
DEBT_SHORT = ("LongTermDebtCurrent", "DebtCurrent")
SHARES = ("EntityCommonStockSharesOutstanding",)  # dei taxonomy


@dataclass(frozen=True, slots=True)
class Fact:
    concept: str
    unit: str
    start: date | None
    end: date
    value: Decimal
    form: str
    fp: str
    known_time: datetime

    @property
    def days(self) -> int | None:
        return (self.end - self.start).days + 1 if self.start else None


def facts(
    clean: CleanStore,
    cik: str,
    concept: str,
    *,
    as_of: datetime,
    unit: str = "USD",
    taxonomy: str = "us-gaap",
) -> list[Fact]:
    """The latest value known at ``as_of`` for each period of one concept, by end date."""
    rows = clean.query(
        sec.DATASET,
        as_of=ensure_utc(as_of),
        where={"cik": sec.cik10(cik), "taxonomy": taxonomy, "concept": concept, "unit": unit},
    )
    out = [
        Fact(
            concept,
            unit,
            date.fromisoformat(r["start"]) if r["start"] else None,
            r["valid_date"],
            Decimal(r["val"]),
            r["form"],
            r["fp"],
            r["known_time"],
        )
        for r in rows
    ]
    return sorted(out, key=lambda f: (f.end, f.days or 0))


def _annual(f: Fact) -> bool:
    return f.days is not None and 350 <= f.days <= 380


@dataclass(frozen=True, slots=True)
class Value:
    value: Decimal
    end: date
    basis: str  # how it was computed, e.g. "FY 2025 + YTD 2026-06 - YTD 2025-06"
    concept: str


def ttm(flow: Sequence[Fact]) -> Value | None:
    """Trailing twelve months of a flow concept (see module docstring)."""
    annual = [f for f in flow if _annual(f)]
    if not annual:
        return None
    a = annual[-1]
    newer = [
        f
        for f in flow
        if f.start is not None
        and f.start == a.end + timedelta(days=1)
        and f.end > a.end
        and not _annual(f)
    ]
    if not newer:
        return Value(a.value, a.end, f"FY to {a.end}", a.concept)
    y = max(newer, key=lambda f: f.end)
    assert a.start is not None
    prior = [
        f
        for f in flow
        if f.start == a.start
        and f.days is not None
        and y.days is not None
        and abs((f.end - (y.end - timedelta(days=365))).days) <= 7
    ]
    if not prior:
        return Value(
            a.value, a.end, f"FY to {a.end} (no prior-year YTD to roll forward)", a.concept
        )
    p = prior[-1]
    return Value(
        a.value + y.value - p.value,
        y.end,
        f"FY to {a.end} + YTD to {y.end} - YTD to {p.end}",
        a.concept,
    )


def latest_instant(items: Sequence[Fact]) -> Value | None:
    inst = [f for f in items if f.start is None]
    if not inst:
        return None
    f = inst[-1]
    return Value(f.value, f.end, f"as of {f.end} ({f.form} {f.fp})", f.concept)


def first_available(
    clean: CleanStore,
    cik: str,
    concepts: Sequence[str],
    *,
    as_of: datetime,
    flow: bool,
    unit: str = "USD",
    taxonomy: str = "us-gaap",
) -> Value | None:
    """TTM (flows) or latest value (instants) of the first concept the company reports."""
    best: Value | None = None
    for c in concepts:
        items = facts(clean, cik, c, as_of=as_of, unit=unit, taxonomy=taxonomy)
        v = ttm(items) if flow else latest_instant(items)
        if v is not None and (best is None or v.end > best.end):
            best = v  # prefer the most recent among the alternatives
    return best


@dataclass
class Snapshot:
    """What was known about a company at ``as_of``. None: not reported / not yet filed."""

    cik: str
    as_of: datetime
    revenue: Value | None = None
    net_income: Value | None = None
    operating_income: Value | None = None
    d_and_a: Value | None = None
    cfo: Value | None = None
    capex: Value | None = None
    equity: Value | None = None
    cash: Value | None = None
    debt_long: Value | None = None
    debt_short: Value | None = None
    shares: Value | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ebitda(self) -> Decimal | None:
        if self.operating_income is None or self.d_and_a is None:
            return None
        return self.operating_income.value + self.d_and_a.value

    @property
    def fcf(self) -> Decimal | None:
        if self.cfo is None or self.capex is None:
            return None
        return self.cfo.value - self.capex.value

    @property
    def net_debt(self) -> Decimal | None:
        if self.cash is None or (self.debt_long is None and self.debt_short is None):
            return None
        debt = (self.debt_long.value if self.debt_long else Decimal(0)) + (
            self.debt_short.value if self.debt_short else Decimal(0)
        )
        return debt - self.cash.value


def snapshot(clean: CleanStore, cik: str, *, as_of: datetime) -> Snapshot:
    s = Snapshot(sec.cik10(cik), ensure_utc(as_of))
    s.revenue = first_available(clean, cik, REVENUE, as_of=as_of, flow=True)
    s.net_income = first_available(clean, cik, NET_INCOME, as_of=as_of, flow=True)
    s.operating_income = first_available(clean, cik, OPERATING_INCOME, as_of=as_of, flow=True)
    s.d_and_a = first_available(clean, cik, D_AND_A, as_of=as_of, flow=True)
    s.cfo = first_available(clean, cik, CFO, as_of=as_of, flow=True)
    s.capex = first_available(clean, cik, CAPEX, as_of=as_of, flow=True)
    s.equity = first_available(clean, cik, EQUITY, as_of=as_of, flow=False)
    s.cash = first_available(clean, cik, CASH, as_of=as_of, flow=False)
    s.debt_long = first_available(clean, cik, DEBT_LONG, as_of=as_of, flow=False)
    s.debt_short = first_available(clean, cik, DEBT_SHORT, as_of=as_of, flow=False)
    s.shares = first_available(
        clean, cik, SHARES, as_of=as_of, flow=False, unit="shares", taxonomy="dei"
    )
    for name in ("revenue", "net_income", "equity", "shares"):
        if getattr(s, name) is None:
            s.notes.append(f"{name}: not reported or not yet filed at {s.as_of:%Y-%m-%d}")
    return s


def ticker_ciks(lake_root: Path) -> dict[str, str]:
    """Ticker -> CIK from the newest stored SEC ticker file (empty before the first fetch)."""
    files = sorted((lake_root / "raw" / sec.SOURCE / "tickers").rglob("*.json"))
    files = [f for f in files if not f.name.endswith(".meta.json")]
    if not files:
        return {}
    return sec.parse_tickers(files[-1].read_bytes())
