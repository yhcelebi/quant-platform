"""FxRate: an exact conversion rate between two currencies.

``FxRate(base=USD, quote=TRY, rate=Decimal("40"))`` means 1 USD = 40 TRY. It converts
base -> quote only; use :meth:`FxRate.inverse` for the other direction so the direction of
every conversion is explicit. Where the rate came from (source, valid_time, known_time) is
tracked by the data layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from qp.core.currency import Currency
from qp.core.errors import CurrencyMismatchError, QPValidationError
from qp.core.money import Money
from qp.core.numeric import DecimalLike, to_decimal


@dataclass(frozen=True, slots=True)
class FxRate:
    base: Currency
    quote: Currency
    rate: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.rate, Decimal):
            raise TypeError("FxRate.rate must be Decimal")
        if not self.rate.is_finite() or self.rate <= 0:
            raise QPValidationError(f"FX rate must be positive and finite, got {self.rate}")
        if self.base == self.quote:
            raise QPValidationError("FX rate base and quote must differ")

    @classmethod
    def of(cls, base: Currency | str, quote: Currency | str, rate: DecimalLike) -> FxRate:
        b = base if isinstance(base, Currency) else Currency(base)
        q = quote if isinstance(quote, Currency) else Currency(quote)
        return cls(b, q, to_decimal(rate))

    def convert(self, money: Money) -> Money:
        """Convert base-currency Money into the quote currency (unrounded)."""
        if money.currency != self.base:
            raise CurrencyMismatchError(
                f"{self} converts {self.base} -> {self.quote}, got {money.currency}"
            )
        return Money(money.amount * self.rate, self.quote)

    def inverse(self) -> FxRate:
        return FxRate(self.quote, self.base, Decimal(1) / self.rate)

    def __str__(self) -> str:
        return f"{self.base}/{self.quote}={self.rate}"
