from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from qp.reference.calendars import (
    CalendarError,
    is_session,
    previous_session,
    session_close,
    sessions,
)


def test_bist_2026_religious_holidays_and_half_days() -> None:
    # Eid al-Fitr (Ramazan Bayrami) 20-22 Mar 2026 (Fri-Sun); arife 19 Mar is a half day.
    assert sessions("BIST", date(2026, 3, 18), date(2026, 3, 24)) == [
        date(2026, 3, 18),
        date(2026, 3, 19),
        date(2026, 3, 23),
        date(2026, 3, 24),
    ]
    assert session_close("BIST", date(2026, 3, 19)) == datetime(2026, 3, 19, 9, 30, tzinfo=UTC)
    assert session_close("BIST", date(2026, 3, 18)) == datetime(2026, 3, 18, 15, tzinfo=UTC)
    # Republic Day: 29 Oct holiday, 28 Oct half day
    assert not is_session("BIST", date(2026, 10, 29))
    assert previous_session("BIST", date(2026, 10, 30)) == date(2026, 10, 28)
    assert previous_session("BIST", date(2026, 10, 29)) == date(2026, 10, 28)


def test_tefas_and_tcmb_follow_turkish_business_days() -> None:
    assert sessions("TEFAS", date(2026, 3, 18), date(2026, 3, 24)) == sessions(
        "TCMB", date(2026, 3, 18), date(2026, 3, 24)
    )


def test_us_and_crypto() -> None:
    assert not is_session("NYSE", date(2026, 7, 3))  # Independence Day observed
    assert is_session("BINANCE", date(2026, 7, 4))


def test_unknown_venue_and_out_of_range() -> None:
    with pytest.raises(CalendarError, match="no trading calendar"):
        is_session("MOON", date(2026, 1, 2))
    with pytest.raises(CalendarError, match="outside"):
        is_session("BIST", date(2099, 1, 2))
    with pytest.raises(CalendarError, match="not a BIST trading day"):
        session_close("BIST", date(2026, 10, 29))
