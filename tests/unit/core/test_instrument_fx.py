from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qp.core import (
    TRY,
    USD,
    CurrencyMismatchError,
    FxRate,
    InstrumentId,
    Money,
    QPValidationError,
)


@pytest.mark.parametrize(
    ("text", "symbol", "venue"),
    [
        ("THYAO.BIST", "THYAO", "BIST"),
        ("AAPL.NASDAQ", "AAPL", "NASDAQ"),
        ("BTCUSDT.BINANCE", "BTCUSDT", "BINANCE"),
        ("BRK.B.NYSE", "BRK.B", "NYSE"),
    ],
)
def test_parse(text: str, symbol: str, venue: str) -> None:
    iid = InstrumentId.parse(text)
    assert (iid.symbol, iid.venue) == (symbol, venue)
    assert str(iid) == text


@pytest.mark.parametrize("bad", ["THYAO", "thyao.BIST", "THYAO.bist", ".BIST", "THYAO.", "A B.X"])
def test_parse_rejects_invalid(bad: str) -> None:
    with pytest.raises(QPValidationError):
        InstrumentId.parse(bad)


symbols = st.from_regex(r"[A-Z0-9][A-Z0-9_\-]{0,9}(\.[A-Z0-9]{1,3})?", fullmatch=True)
venues = st.from_regex(r"[A-Z][A-Z0-9_]{0,9}", fullmatch=True)


@given(symbols, venues)
def test_parse_roundtrip(symbol: str, venue: str) -> None:
    iid = InstrumentId(symbol, venue)
    assert InstrumentId.parse(str(iid)) == iid


def test_fx_convert_and_inverse() -> None:
    usdtry = FxRate.of("USD", "TRY", "40.00")
    assert usdtry.convert(Money.of("2001.00", "USD")) == Money.of("80040", TRY)
    assert usdtry.inverse().convert(Money.of("80", "TRY")) == Money.of("2", USD)
    assert str(usdtry) == "USD/TRY=40.00"


def test_fx_wrong_direction_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        FxRate.of("USD", "TRY", "40").convert(Money.of("1", "TRY"))


@pytest.mark.parametrize("rate", ["0", "-1"])
def test_fx_rate_must_be_positive(rate: str) -> None:
    with pytest.raises(QPValidationError):
        FxRate.of("USD", "TRY", rate)


def test_fx_same_currency_rejected() -> None:
    with pytest.raises(QPValidationError):
        FxRate(TRY, TRY, Decimal(1))


def test_fx_split_identity_case_d_shape() -> None:
    """FX effect + price effect equals unrealized P&L in TRY (DOMAIN.md formula)."""
    cost, value = Money.of("2001.00", "USD"), Money.of("2200.00", "USD")
    x0, x1 = FxRate.of("USD", "TRY", "40.00"), FxRate.of("USD", "TRY", "42.00")
    fx_effect = cost.amount * (x1.rate - x0.rate)
    price_effect = (value - cost).amount * x1.rate
    unrealized_try = x1.convert(value) - x0.convert(cost)
    assert fx_effect + price_effect == unrealized_try.amount
