"""FRED (ALFRED vintages) and EVDS connectors with fake transports. Fixture values are invented;
the shapes follow the FRED API docs and TCMB's EVDS 3 guide plus third-party clients."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from qp.data.connectors import evds, fred
from qp.data.connectors.http import HttpClient
from qp.data.lake import CleanStore, RawStore
from qp.platform.lake import LakePaths

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpClient:
    return HttpClient(
        user_agent="t",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


@pytest.fixture
def lake(tmp_path: Path) -> tuple[RawStore, CleanStore]:
    paths = LakePaths(tmp_path)
    return RawStore(paths), CleanStore(paths)


# ---------------------------------------------------------------- FRED
def fred_page(observations: list[dict[str, str]], count: int | None = None) -> bytes:
    return json.dumps(
        {
            "realtime_start": "1776-07-04",
            "realtime_end": "9999-12-31",
            "count": len(observations) if count is None else count,
            "offset": 0,
            "limit": 100000,
            "observations": observations,
        }
    ).encode()


def obs(realtime_start: str, realtime_end: str, day: str, value: str) -> dict[str, str]:
    return {
        "realtime_start": realtime_start,
        "realtime_end": realtime_end,
        "date": day,
        "value": value,
    }


VINTAGES = [  # first release, then a revision; and a "." that is skipped
    obs("2026-01-13", "2026-02-10", "2025-12-01", "320.1"),
    obs("2026-02-11", "9999-12-31", "2025-12-01", "320.4"),
    obs("2026-02-11", "9999-12-31", "2026-01-01", "."),
]


def test_fred_vintages_are_point_in_time(lake: tuple[RawStore, CleanStore]) -> None:
    raw, clean = lake
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, content=fred_page(VINTAGES))

    with client(handler) as c:
        report = fred.fetch_series(c, raw, clean, ["CPIAUCSL"], api_key="KEY123", now=lambda: NOW)
    assert report.ok
    assert report.rows_written == 2
    assert seen[0].params["realtime_start"] == "1776-07-04"
    assert seen[0].params["api_key"] == "KEY123"
    meta = raw.meta(clean.query(fred.DATASET, as_of=NOW)[0]["raw_ref"])
    assert "KEY123" not in json.dumps(meta)  # only the redacted URL is stored
    first_known = fred.vintage_known_time(date(2026, 1, 13))
    assert first_known == datetime(2026, 1, 14, 5, 59, 59, tzinfo=UTC)  # 23:59:59 CST
    on_release = clean.query(fred.DATASET, as_of=first_known)
    assert [r["value"] for r in on_release] == [Decimal("320.1")]
    assert clean.query(fred.DATASET, as_of=first_known - timedelta(seconds=1)) == []
    latest = clean.query(fred.DATASET, as_of=NOW)
    assert [r["value"] for r in latest] == [Decimal("320.4")]
    with client(handler) as c:  # refetch: nothing new
        assert (
            fred.fetch_series(
                c, raw, clean, ["CPIAUCSL"], api_key="K", now=lambda: NOW
            ).rows_written
            == 0
        )


def test_fred_paginates_and_reports_api_errors(lake: tuple[RawStore, CleanStore]) -> None:
    raw, clean = lake
    offsets: list[str] = []

    def paged(request: httpx.Request) -> httpx.Response:
        offsets.append(request.url.params["offset"])
        page = VINTAGES[:1] if request.url.params["offset"] == "0" else VINTAGES[1:2]
        return httpx.Response(200, content=fred_page(page, count=2))

    with client(paged) as c:
        report = fred.fetch_series(c, raw, clean, ["CPIAUCSL"], api_key="K", now=lambda: NOW)
    assert offsets == ["0", "1"]
    assert report.rows_written == 2

    def bad_key(request: httpx.Request) -> httpx.Response:
        message = "Bad Request.  The value for variable api_key is not registered."
        body = {"error_code": 400, "error_message": message}
        return httpx.Response(400, content=json.dumps(body).encode())

    with client(bad_key) as c:
        report = fred.fetch_series(c, raw, clean, ["X"], api_key="K", now=lambda: NOW)
    assert "not registered" in report.errors[0]


# ---------------------------------------------------------------- EVDS
def evds_body(code: str, items: list[tuple[str, str | None]]) -> bytes:
    col = code.replace(".", "_")
    return json.dumps(
        {
            "totalCount": len(items),
            "items": [
                {"Tarih": t, col: v, "UNIXTIME": {"$numberLong": "1704056400"}} for t, v in items
            ],
        }
    ).encode()


def test_evds_period_parsing() -> None:
    assert evds.period_start("16-10-2026") == date(2026, 10, 16)
    assert evds.period_start("2024-1") == date(2024, 1, 1)
    assert evds.period_start("2024-12") == date(2024, 12, 1)
    assert evds.period_start("2024-Q3") == date(2024, 7, 1)
    assert evds.period_start("2024") == date(2024, 1, 1)
    with pytest.raises(evds.EvdsError, match="unknown Tarih"):
        evds.period_start("2024/01")


def test_evds_year_windows_start_on_period_boundaries() -> None:
    assert evds.year_windows(date(2024, 5, 3), date(2026, 2, 1)) == [
        (date(2024, 1, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 2, 1)),
    ]


def test_evds_fetch_uses_header_key_and_path_params(lake: tuple[RawStore, CleanStore]) -> None:
    raw, clean = lake
    requests: list[httpx.Request] = []
    code = "TP.TUKFIY2025.GENEL"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if b"startDate=01-01-2025" in request.url.raw_path:
            return httpx.Response(
                200, content=evds_body(code, [("2025-11", "105.2"), ("2025-12", "106.0")])
            )
        return httpx.Response(200, content=evds_body(code, [("2026-1", "108.1"), ("2026-2", None)]))

    with client(handler) as c:
        report = evds.fetch_series(
            c, raw, clean, [code], date(2025, 6, 1), date(2026, 10, 19),
            base_url="https://evds3.tcmb.gov.tr/igmevdsms-dis/", api_key="EKEY", now=lambda: NOW,
        )  # fmt: skip
    assert report.ok, report.errors
    assert report.rows_written == 3  # the null value is skipped
    assert requests[0].headers["key"] == "EKEY"
    assert requests[0].url.raw_path == (
        b"/igmevdsms-dis/series=TP.TUKFIY2025.GENEL&startDate=01-01-2025&endDate=31-12-2025&type=json"
    )
    assert "EKEY" not in requests[0].url.raw_path.decode()
    rows = clean.query(evds.DATASET, as_of=NOW)
    assert (rows[0]["valid_date"], rows[0]["period"], rows[0]["value"]) == (
        date(2025, 11, 1),
        "2025-11",
        Decimal("105.2"),
    )
    assert {r["known_time_basis"] for r in rows} == {"fetched"}  # EVDS has no vintages
    assert clean.query(evds.DATASET, as_of=NOW - timedelta(seconds=1)) == []


def test_evds_stops_on_bad_key(lake: tuple[RawStore, CleanStore]) -> None:
    raw, clean = lake
    body = b'{"status":"403","message":"Required request header \'key\' is not present"}'
    with client(lambda _: httpx.Response(403, content=body)) as c:
        report = evds.fetch_series(
            c, raw, clean, ["TP.X", "TP.Y"], date(2020, 1, 1), date(2026, 1, 1),
            base_url="https://e/", api_key="", now=lambda: NOW,
        )  # fmt: skip
    assert report.errors == ["TP.X: HTTP 403 (missing or wrong API key)"]
    with pytest.raises(evds.EvdsError, match="invalid series code"):
        evds.request_url("https://e/", "TP.X&type=csv", date(2020, 1, 1), date(2020, 2, 1))
