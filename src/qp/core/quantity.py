"""Quantity: an exact, possibly fractional, number of units of an instrument."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from qp.core.errors import FloatNotAllowedError
from qp.core.numeric import DecimalLike, to_decimal


def _scalar(other: object) -> Decimal | None:
    """Decimal value of a plain scalar operand, or None if the type is not supported."""
    if isinstance(other, float):
        raise FloatNotAllowedError("cannot combine Quantity with a float")
    if isinstance(other, Decimal | int) and not isinstance(other, bool):
        return to_decimal(other)
    return None


@dataclass(frozen=True, slots=True, order=True)
class Quantity:
    """Units held or traded. Negative values mean short or outgoing."""

    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            raise TypeError(f"Quantity.value must be Decimal, got {type(self.value).__name__}")
        if not self.value.is_finite():
            raise ValueError(f"Quantity.value must be finite, got {self.value}")

    @classmethod
    def of(cls, value: DecimalLike) -> Quantity:
        return cls(to_decimal(value))

    def is_zero(self) -> bool:
        return self.value == 0

    def __add__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            raise TypeError("can only add Quantity to Quantity")
        return Quantity(self.value + other.value)

    def __sub__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            raise TypeError("can only subtract Quantity from Quantity")
        return Quantity(self.value - other.value)

    def __neg__(self) -> Quantity:
        return Quantity(-self.value)

    def __abs__(self) -> Quantity:
        return Quantity(abs(self.value))

    def __mul__(self, other: Decimal | int) -> Quantity:
        factor = _scalar(other)
        if factor is None:
            return NotImplemented  # lets Money.__rmul__ handle Quantity * Money
        return Quantity(self.value * factor)

    __rmul__ = __mul__

    def __truediv__(self, other: Decimal | int) -> Quantity:
        divisor = _scalar(other)
        if divisor is None:
            return NotImplemented
        return Quantity(self.value / divisor)

    def ratio(self, other: Quantity) -> Decimal:
        """``self / other`` as a plain Decimal."""
        if not isinstance(other, Quantity):
            raise TypeError("ratio() needs a Quantity")
        return self.value / other.value

    def __str__(self) -> str:
        return str(self.value)
