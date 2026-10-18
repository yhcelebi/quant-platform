from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from qp.core import (
    ISTANBUL,
    FloatNotAllowedError,
    ManualClock,
    NaiveDatetimeError,
    QPValidationError,
    SystemClock,
    assume_istanbul,
    ensure_utc,
    float_to_decimal,
    istanbul_date,
    parse_utc,
    to_decimal,
    to_float64,
    to_float64_list,
    to_istanbul,
)
from qp.core.clock import Clock


def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(NaiveDatetimeError):
        ensure_utc(datetime(2025, 1, 1))  # noqa: DTZ001 - deliberately naive


def test_ensure_utc_converts() -> None:
    plus3 = datetime(2025, 1, 1, 10, tzinfo=timezone(timedelta(hours=3)))
    assert ensure_utc(plus3) == datetime(2025, 1, 1, 7, tzinfo=UTC)
    assert ensure_utc(plus3).tzinfo is UTC


def test_ensure_utc_rejects_non_datetime() -> None:
    with pytest.raises(TypeError):
        ensure_utc(date(2025, 1, 1))  # type: ignore[arg-type]


def test_istanbul_display_and_date() -> None:
    late_utc = datetime(2025, 1, 1, 22, 30, tzinfo=UTC)
    assert to_istanbul(late_utc).hour == 1
    assert to_istanbul(late_utc).tzinfo == ISTANBUL
    assert istanbul_date(late_utc) == date(2025, 1, 2)


def test_assume_istanbul() -> None:
    local = datetime(2025, 7, 2, 18, 0)  # noqa: DTZ001 - naive local wall time on purpose
    assert assume_istanbul(local) == datetime(2025, 7, 2, 15, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="already aware"):
        assume_istanbul(datetime(2025, 1, 1, tzinfo=UTC))


def test_assume_istanbul_historical_dst() -> None:
    # Turkey observed DST (UTC+3) in summer 2015; winter was UTC+2.
    assert assume_istanbul(datetime(2015, 1, 15, 12)) == datetime(2015, 1, 15, 10, tzinfo=UTC)  # noqa: DTZ001


def test_parse_utc() -> None:
    assert parse_utc("2025-01-01T10:00:00+03:00") == datetime(2025, 1, 1, 7, tzinfo=UTC)
    assert parse_utc("2025-01-01T07:00:00Z") == datetime(2025, 1, 1, 7, tzinfo=UTC)
    with pytest.raises(NaiveDatetimeError):
        parse_utc("2025-01-01T07:00:00")


def test_system_clock_is_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert isinstance(SystemClock(), Clock)


def test_manual_clock() -> None:
    clock = ManualClock(datetime(2025, 1, 1, tzinfo=UTC))
    clock.advance(timedelta(days=1))
    assert clock.now() == datetime(2025, 1, 2, tzinfo=UTC)
    clock.set(datetime(2024, 1, 1, 3, tzinfo=timezone(timedelta(hours=3))))
    assert clock.now() == datetime(2024, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(timedelta(seconds=-1))
    with pytest.raises(NaiveDatetimeError):
        ManualClock(datetime(2025, 1, 1))  # noqa: DTZ001


def test_to_decimal() -> None:
    assert to_decimal("1.10") == Decimal("1.10")
    assert to_decimal(" 2 ") == Decimal(2)
    assert to_decimal(3) == Decimal(3)
    with pytest.raises(FloatNotAllowedError):
        to_decimal(0.1)  # type: ignore[arg-type]
    with pytest.raises(QPValidationError):
        to_decimal("abc")
    with pytest.raises(QPValidationError):
        to_decimal("NaN")
    with pytest.raises(QPValidationError):
        to_decimal(True)


def test_float_boundary() -> None:
    assert to_float64(Decimal("0.1")) == 0.1
    assert to_float64_list([Decimal("1.5"), 2]) == [1.5, 2.0]
    assert float_to_decimal(0.1 + 0.2, places=10) == Decimal("0.3000000000")
    with pytest.raises(QPValidationError):
        float_to_decimal(float("nan"), places=2)
