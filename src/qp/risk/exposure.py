"""Exposure and concentration as weights of the scope value (ADR 0010, 16)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from qp.risk.var import Holding

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Exposure:
    name: str
    value_try: Decimal
    weight: Decimal


@dataclass
class ExposureReport:
    total_try: Decimal
    by_currency: list[Exposure] = field(default_factory=list)
    by_asset_class: list[Exposure] = field(default_factory=list)
    by_account: list[Exposure] = field(default_factory=list)
    by_holding: list[Exposure] = field(default_factory=list)
    herfindahl: Decimal = ZERO  # sum of squared holding weights

    @property
    def effective_holdings(self) -> Decimal | None:
        """1 / HHI: the number of equal-weight holdings with the same concentration."""
        return Decimal(1) / self.herfindahl if self.herfindahl else None


@dataclass(frozen=True, slots=True)
class AccountHolding:
    account: str
    holding: Holding


def _ranked(values: dict[str, Decimal], total: Decimal) -> list[Exposure]:
    return sorted(
        (Exposure(k, v, v / total if total else ZERO) for k, v in values.items()),
        key=lambda e: (-e.value_try, e.name),
    )


def exposures(
    items: Sequence[AccountHolding], asset_class: Callable[[Holding], str]
) -> ExposureReport:
    """Weights by currency, asset class, account and holding (accounts aggregated)."""
    total = sum((i.holding.value_try for i in items), ZERO)
    currency: dict[str, Decimal] = defaultdict(Decimal)
    klass: dict[str, Decimal] = defaultdict(Decimal)
    account: dict[str, Decimal] = defaultdict(Decimal)
    holding: dict[str, Decimal] = defaultdict(Decimal)
    for i in items:
        h = i.holding
        currency[h.currency.code] += h.value_try
        klass[asset_class(h)] += h.value_try
        account[i.account] += h.value_try
        holding[h.name] += h.value_try
    report = ExposureReport(
        total,
        _ranked(currency, total),
        _ranked(klass, total),
        _ranked(account, total),
        _ranked(holding, total),
    )
    report.herfindahl = sum((e.weight * e.weight for e in report.by_holding), ZERO)
    return report
