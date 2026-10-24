"""Market histories, the preloaded point-query view and index/rate series (point in time)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from qp.core import TRY, USD, InstrumentId
from qp.data.connectors import evds, tcmb
from qp.data.lake import CleanStore
from qp.data.lake_market import LakeMarketData
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import FxPoint, LayeredMarketData, PreloadedMarketData, PricePoint, latest_on
from qp.data.series import LakeSeries, LayeredSeries, ManualSeries, SeriesError, SeriesPoint
from qp.platform.lake import LakePaths

THY = InstrumentId.parse("THYAO.BIST")
D0 = date(2026, 1, 1)
LATE = datetime(2027, 1, 1, tzinfo=UTC)


def p(day: int, value: str, known: datetime | None = None, src: str = "m") -> PricePoint:
    d = D0 + timedelta(days=day)
    return PricePoint(THY, d, Decimal(value), TRY, src, known or default_known_time(d))


def fx(day: int, value: str, src: str = "m") -> FxPoint:
    d = D0 + timedelta(days=day)
    return FxPoint(USD, d, Decimal(value), src, default_known_time(d))


def test_manual_history_takes_latest_known_revision_per_date() -> None:
    early = datetime(2026, 1, 2, 9, tzinfo=UTC)
    late = datetime(2026, 1, 5, tzinfo=UTC)
    md = ManualMarketData([p(0, "10", early), p(0, "11", late), p(3, "12")], [])
    assert [x.price for x in md.price_history(THY, D0 + timedelta(days=9), as_of=LATE)] == [
        Decimal("11"),
        Decimal("12"),
    ]
    assert [x.price for x in md.price_history(THY, D0 + timedelta(days=9), as_of=early)] == [
        Decimal("10")
    ]
    assert md.price_history(THY, D0 + timedelta(days=2), as_of=LATE)[-1].valid_date == D0


@given(
    st.lists(st.tuples(st.integers(0, 20), st.integers(1, 99)), max_size=8),
    st.lists(st.tuples(st.integers(0, 20), st.integers(1, 99)), max_size=8),
)
def test_layered_history_agrees_with_point_queries(
    first: list[tuple[int, int]], second: list[tuple[int, int]]
) -> None:
    def source(items: list[tuple[int, int]], src: str) -> ManualMarketData:
        uniq = dict(items)
        return ManualMarketData([p(d, str(v), src=src) for d, v in uniq.items()], [])

    layered = LayeredMarketData([source(first, "a"), source(second, "b")])
    history = layered.price_history(THY, D0 + timedelta(days=25), as_of=LATE)
    for day in range(25):
        on = D0 + timedelta(days=day)
        assert latest_on(history, on) == layered.price(THY, on, as_of=LATE)


def test_preloaded_matches_source_and_refuses_other_views() -> None:
    md = ManualMarketData([p(0, "10"), p(2, "12")], [fx(1, "40")])
    pre = PreloadedMarketData(md, as_of=LATE, until=D0 + timedelta(days=5))
    for day in range(6):
        on = D0 + timedelta(days=day)
        assert pre.price(THY, on, as_of=LATE) == md.price(THY, on, as_of=LATE)
        assert pre.fx_try(USD, on, as_of=LATE) == md.fx_try(USD, on, as_of=LATE)
    with pytest.raises(ValueError, match="preloaded until"):
        pre.price(THY, D0 + timedelta(days=6), as_of=LATE)
    with pytest.raises(ValueError, match="preloaded for as_of"):
        pre.price(THY, D0, as_of=LATE - timedelta(days=1))


def _tcmb_row(day: date, value: str, known: datetime) -> dict[str, Any]:
    return {
        "currency": "USD",
        "rate_kind": "forex_buying",
        "valid_date": day,
        "unit": 1,
        "value": Decimal(value),
        "source": tcmb.SOURCE,
        "known_time": known,
        "known_time_basis": "schedule",
        "fetched_at": known,
        "raw_ref": "raw/x",
    }


def test_lake_fx_history_is_point_in_time(tmp_path: Path) -> None:
    clean = CleanStore(LakePaths(tmp_path))
    k1, k2 = datetime(2026, 1, 1, 13, tzinfo=UTC), datetime(2026, 1, 2, 13, tzinfo=UTC)
    clean.append(tcmb.DATASET, [_tcmb_row(D0, "40", k1), _tcmb_row(D0 + timedelta(1), "41", k2)])
    md = LakeMarketData(clean)
    assert [x.rate_try for x in md.fx_history(USD, D0 + timedelta(5), as_of=LATE)] == [
        Decimal("40"),
        Decimal("41"),
    ]
    assert [x.rate_try for x in md.fx_history(USD, D0 + timedelta(5), as_of=k1)] == [Decimal(40)]
    assert md.price_history(THY, D0, as_of=LATE) == []


# ---------------------------------------------------------------- series
def test_manual_series_requires_known_time(tmp_path: Path) -> None:
    path = tmp_path / "series.csv"
    path.write_text(
        "valid_date,series,value,source,known_time\n"
        "2026-01-01,TUFE,100,tuik,2026-02-03T10:00:00+03:00\n"
        "2026-02-01,TUFE,105,tuik,2026-03-03T10:00:00+03:00\n"
    )
    s = ManualSeries.from_file(path)
    as_of = datetime(2026, 3, 1, tzinfo=UTC)  # February not yet published
    assert [x.value for x in s.history("TUFE", date(2026, 12, 1), as_of=as_of)] == [Decimal(100)]
    path.write_text("valid_date,series,value,source,known_time\n2026-01-01,TUFE,100,tuik,\n")
    with pytest.raises(SeriesError, match="known_time is required"):
        ManualSeries.from_file(path)
    assert ManualSeries.from_file(tmp_path / "absent.csv").history("TUFE", D0, as_of=LATE) == []


def test_lake_and_layered_series(tmp_path: Path) -> None:
    clean = CleanStore(LakePaths(tmp_path))
    fetched = datetime(2026, 5, 1, tzinfo=UTC)
    obs = [
        {"series_code": "TP.X", "valid_date": date(2026, m, 1), "period": f"2026-{m}"}
        | {"value": Decimal(100 + m)}
        for m in (1, 2, 3)
    ]
    clean.append(evds.DATASET, evds.to_rows(obs, raw_ref="raw/x", fetched_at=fetched))
    lake = LakeSeries(clean)
    assert [x.value for x in lake.history("evds:TP.X", date(2026, 2, 1), as_of=LATE)] == [
        Decimal(101),
        Decimal(102),
    ]
    assert lake.history("evds:TP.X", date(2026, 12, 1), as_of=fetched - timedelta(1)) == []
    assert lake.history("TUFE", date(2026, 12, 1), as_of=LATE) == []
    manual = ManualSeries(
        [SeriesPoint("evds:TP.X", date(2026, 2, 1), Decimal(999), "manual", fetched)]
    )
    layered = LayeredSeries([manual, lake])
    assert [x.value for x in layered.history("evds:TP.X", date(2026, 12, 1), as_of=LATE)] == [
        Decimal(101),
        Decimal(999),
        Decimal(103),
    ]
