"""Exact-number helpers and the single Decimal <-> float64 boundary (rule 3).

Ledger and portfolio code uses Decimal. Analytics (stats, risk, backtests) may use float64,
but only after converting here, so every conversion is explicit and searchable.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

from qp.core.errors import FloatNotAllowedError, QPValidationError

type DecimalLike = Decimal | int | str


def to_decimal(value: DecimalLike) -> Decimal:
    """Convert an exact value to a finite Decimal. Floats are rejected, not rounded."""
    if isinstance(value, bool):
        raise QPValidationError("bool is not a number")
    if isinstance(value, float):
        raise FloatNotAllowedError(
            f"float {value!r} is not allowed for exact amounts; pass a str or Decimal"
        )
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int | str):
        try:
            result = Decimal(value.strip() if isinstance(value, str) else value)
        except InvalidOperation as exc:
            raise QPValidationError(f"not a decimal number: {value!r}") from exc
    else:
        raise TypeError(f"cannot convert {type(value).__name__} to Decimal")
    if not result.is_finite():
        raise QPValidationError(f"non-finite decimal: {value!r}")
    return result


def to_float64(value: Decimal | int) -> float:
    """The one explicit Decimal -> float64 boundary for analytics."""
    if isinstance(value, float):
        return value
    return float(value)


def to_float64_list(values: Iterable[Decimal | int]) -> list[float]:
    """Vector form of :func:`to_float64`."""
    return [to_float64(v) for v in values]


def float_to_decimal(value: float, *, places: int) -> Decimal:
    """Bring an analytics float back to Decimal, explicitly rounded to ``places`` decimals.

    Uses the shortest repr of the float, then quantizes half-even.
    """
    if value != value or value in (float("inf"), float("-inf")):
        raise QPValidationError(f"non-finite float: {value!r}")
    return Decimal(repr(value)).quantize(Decimal(1).scaleb(-places))
