from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qp.core import (
    TRY,
    USD,
    CurrencyMismatchError,
    FloatNotAllowedError,
    Money,
    QPValidationError,
    Quantity,
)

# Realistic domain: |amount| < 1e12 with at most 10 decimal places (<= 22 significant digits).
# Inside this domain Decimal's default 28-digit context makes +/- exact (see ADR 0004).
amounts = st.decimals(
    allow_nan=False, allow_infinity=False, min_value=-(10**12), max_value=10**12, places=10
)


def try_(x: str) -> Money:
    return Money.of(x, "TRY")


def test_of_accepts_str_int_decimal() -> None:
    assert Money.of("10.50", "TRY") == Money(Decimal("10.50"), TRY)
    assert Money.of(10, TRY).amount == Decimal(10)
    assert Money.of(Decimal("1.1"), "USD").currency == USD


@pytest.mark.parametrize("bad", [1.5, float("nan")])
def test_of_rejects_float(bad: float) -> None:
    with pytest.raises(FloatNotAllowedError):
        Money.of(bad, "TRY")  # type: ignore[arg-type]


def test_constructor_requires_decimal() -> None:
    with pytest.raises(TypeError):
        Money(1.5, TRY)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        Money(Decimal("NaN"), TRY)


def test_non_finite_string_rejected() -> None:
    with pytest.raises(QPValidationError):
        Money.of("Infinity", "TRY")


def test_same_currency_arithmetic() -> None:
    assert try_("1.00") + try_("2.50") == try_("3.50")
    assert try_("1.00") - try_("2.50") == try_("-1.50")
    assert -try_("1") == try_("-1")
    assert abs(try_("-1")) == try_("1")


def test_currency_mismatch_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = try_("1") + Money.of("1", "USD")
    with pytest.raises(CurrencyMismatchError):
        _ = try_("1") < Money.of("1", "USD")


def test_scalar_multiplication_and_division() -> None:
    assert try_("10.00") * 3 == try_("30.00")
    assert 3 * try_("10.00") == try_("30.00")
    assert try_("10") * Decimal("0.5") == try_("5")
    assert try_("3003") / Quantity.of(200) == try_("15.015")
    assert Quantity.of(50) * try_("15.015") == try_("750.750")


def test_multiplying_by_float_is_rejected() -> None:
    with pytest.raises(FloatNotAllowedError):
        _ = try_("1") * 1.5  # type: ignore[operator]


def test_money_times_money_is_type_error() -> None:
    with pytest.raises(TypeError):
        _ = try_("1") * try_("2")  # type: ignore[operator]


def test_ratio() -> None:
    assert try_("750.75").ratio(try_("3003")) == Decimal("0.25")


def test_no_implicit_rounding() -> None:
    avg = try_("2252.25") / 300
    assert avg.amount == Decimal("7.5075")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2.345", "2.34"), ("2.355", "2.36"), ("-2.345", "-2.34"), ("1", "1.00")],
)
def test_quantize_half_even(raw: str, expected: str) -> None:
    assert str(try_(raw).quantize().amount) == expected


def test_quantize_zero_minor_units() -> None:
    assert Money.of("100.5", "JPY").quantize().amount == Decimal("100")


def test_total() -> None:
    assert Money.total([try_("1"), try_("2")], TRY) == try_("3")
    assert Money.total([], TRY) == Money.zero(TRY)
    with pytest.raises(CurrencyMismatchError):
        Money.total([Money.of("1", "USD")], TRY)


def test_ordering_and_str() -> None:
    assert try_("1") < try_("2") <= try_("2") < try_("3")
    assert try_("3") > try_("2") >= try_("2")
    assert str(try_("1.50")) == "1.50 TRY"


@given(amounts, amounts, amounts)
def test_addition_is_associative_and_commutative(a: Decimal, b: Decimal, c: Decimal) -> None:
    x, y, z = Money(a, TRY), Money(b, TRY), Money(c, TRY)
    assert (x + y) + z == x + (y + z)
    assert x + y == y + x


@given(amounts, amounts)
def test_sub_inverts_add(a: Decimal, b: Decimal) -> None:
    x, y = Money(a, TRY), Money(b, TRY)
    assert (x + y) - y == x


@given(amounts)
def test_quantize_is_idempotent_and_close(a: Decimal) -> None:
    q = Money(a, TRY).quantize()
    assert q.quantize() == q
    assert abs(q.amount - a) <= Decimal("0.005")


def test_decimal_context_precision_is_documented() -> None:
    """Default context: 28 significant digits. Beyond that, results round (ADR 0004)."""
    tiny = Decimal("1E-30")
    assert (Money.of("1", "TRY") + Money(tiny, TRY)).amount == Decimal("1")
    exact = Money.of("999999999999.9999999999", "TRY") + Money.of("0.0000000001", "TRY")
    assert exact.amount == Decimal("1000000000000.0000000000")
