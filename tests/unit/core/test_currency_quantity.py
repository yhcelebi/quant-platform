from decimal import Decimal

import pytest

from qp.core import TRY, Currency, FloatNotAllowedError, Quantity, UnknownCurrencyError


def test_known_currency_and_minor_units() -> None:
    assert Currency("TRY") == TRY
    assert TRY.minor_units == 2
    assert Currency("JPY").minor_units == 0
    assert Currency("KWD").minor_units == 3
    assert str(TRY) == "TRY"


@pytest.mark.parametrize("bad", ["try", "XXX", "TL", "", "BTC"])
def test_unknown_currency_rejected(bad: str) -> None:
    with pytest.raises(UnknownCurrencyError):
        Currency(bad)


def test_quantity_arithmetic() -> None:
    a, b = Quantity.of("100"), Quantity.of("50.5")
    assert a + b == Quantity.of("150.5")
    assert a - b == Quantity.of("49.5")
    assert a * 2 == Quantity.of("200")
    assert 2 * a == Quantity.of("200")
    assert a / 4 == Quantity.of("25")
    assert a.ratio(Quantity.of("200")) == Decimal("0.5")
    assert -a == Quantity.of("-100")
    assert abs(Quantity.of("-1")) == Quantity.of("1")
    assert Quantity.of("0").is_zero()
    assert a > b


def test_quantity_supports_fractional_crypto() -> None:
    assert Quantity.of("0.00012345").value == Decimal("0.00012345")


def test_quantity_rejects_float_and_bad_types() -> None:
    with pytest.raises(FloatNotAllowedError):
        Quantity.of(1.0)  # type: ignore[arg-type]
    with pytest.raises(FloatNotAllowedError):
        _ = Quantity.of(1) * 1.5  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Quantity.of(1) + 1  # type: ignore[operator]
    with pytest.raises(TypeError):
        Quantity(1)  # type: ignore[arg-type]
