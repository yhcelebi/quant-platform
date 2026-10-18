"""Money: an exact Decimal amount with an explicit currency (rule 3).

- Arithmetic between Money values requires the same currency (CurrencyMismatchError).
- Floats are rejected everywhere (FloatNotAllowedError).
- Nothing rounds implicitly. :meth:`Money.quantize` rounds to the currency's minor unit,
  half-even by default, and is called only where cash is actually booked.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from qp.core.currency import Currency
from qp.core.errors import CurrencyMismatchError, FloatNotAllowedError
from qp.core.numeric import DecimalLike, to_decimal
from qp.core.quantity import Quantity

type Scalar = Decimal | int | Quantity


def _scalar(value: object) -> Decimal | None:
    """Decimal value of a scalar operand, or None if the type is not supported."""
    if isinstance(value, float):
        raise FloatNotAllowedError("cannot combine Money with a float")
    if isinstance(value, Quantity):
        return value.value
    if isinstance(value, Decimal | int) and not isinstance(value, bool):
        return to_decimal(value)
    return None


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact amount in a currency, e.g. ``Money.of("10.50", "TRY")``."""

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money.amount must be Decimal, got {type(self.amount).__name__}")
        if not self.amount.is_finite():
            raise ValueError(f"Money.amount must be finite, got {self.amount}")
        if not isinstance(self.currency, Currency):
            raise TypeError("Money.currency must be a Currency")

    @classmethod
    def of(cls, amount: DecimalLike, currency: Currency | str) -> Money:
        """Build Money from a str/int/Decimal amount and a currency or ISO code."""
        cur = currency if isinstance(currency, Currency) else Currency(currency)
        return cls(to_decimal(amount), cur)

    @classmethod
    def zero(cls, currency: Currency | str) -> Money:
        return cls.of(0, currency)

    @staticmethod
    def total(items: Iterable[Money], currency: Currency) -> Money:
        """Sum Money values; all must be in ``currency``. Empty input gives zero."""
        result = Money(Decimal(0), currency)
        for item in items:
            result = result + item
        return result

    # ---------------------------------------------------------------- helpers
    def _check(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} and {other.currency} without FX conversion"
            )

    def quantize(self, rounding: str = ROUND_HALF_EVEN) -> Money:
        """Round to the currency's minor unit (TRY: 0.01). Explicit; never implicit."""
        exp = Decimal(1).scaleb(-self.currency.minor_units)
        return Money(self.amount.quantize(exp, rounding=rounding), self.currency)

    def is_zero(self) -> bool:
        return self.amount == 0

    # ---------------------------------------------------------------- arithmetic
    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def __mul__(self, other: Scalar) -> Money:
        factor = _scalar(other)
        if factor is None:
            return NotImplemented
        return Money(self.amount * factor, self.currency)

    __rmul__ = __mul__

    def __truediv__(self, other: Scalar) -> Money:
        divisor = _scalar(other)
        if divisor is None:
            return NotImplemented
        return Money(self.amount / divisor, self.currency)

    def ratio(self, other: Money) -> Decimal:
        """``self / other`` as a plain Decimal; both must share a currency."""
        self._check(other)
        return self.amount / other.amount

    # ---------------------------------------------------------------- ordering
    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.amount} {self.currency.code}"
