"""Economic calendar: FRED release dates connector (fake transport) and calendar.csv."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from qp.data.calendar import CalendarError
from qp.data.connectors import fred
from qp.data.connectors.http import HttpClient
from qp.data.lake import CleanStore, RawStore
from qp.macro.calendar import events
from qp.platform.lake import LakePaths

NOW = datetime(2026, 10, 25, 9, tzinfo=UTC)


def payload(items: list[tuple[int, str, str]], count: int | None = None) -> bytes:
    return json.dumps(
        {
            "count": len(items) if count is None else count,
            "offset": 0,
            "limit": 1000,
            "release_dates": [{"release_id": i, "release_name": n, "date": d} for i, n, d in items],
        }
    ).encode()


def test_release_dates_fetch_and_calendar_view(tmp_path: Path) -> None:
    paths = LakePaths(tmp_path / "lake")
    raw, clean = RawStore(paths), CleanStore(paths)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            content=payload(
                [
                    (10, "Consumer Price Index", "2026-11-12"),
                    (50, "Employment Situation", "2026-11-06"),
                ]
            ),
        )

    client = HttpClient(
        transport=httpx.MockTransport(handler), user_agent="t", min_interval_s=0, retries=0
    )
    with client:
        report = fred.fetch_release_dates(
            client, raw, clean, start=date(2026, 9, 25), api_key="SECRETKEY", now=lambda: NOW
        )
    assert report.ok
    assert report.rows_written == 2
    assert "include_release_dates_with_no_data=true" in seen[0]
    metas = list((tmp_path / "lake").rglob("*.meta.json"))
    assert metas
    assert all("SECRETKEY" not in m.read_text() for m in metas)  # only the redacted URL

    manual = tmp_path / "calendar.csv"
    manual.write_text(
        "day,event,title,time,country,category,source,known_time\n"
        "2026-11-03,tr_cpi,TÜFE Ekim 2026,10:00,TR,inflation,TÜİK,2026-01-02T10:00:00+03:00\n"
        "2026-11-03,tr_cpi,TÜFE Ekim 2026,11:00,TR,inflation,TÜİK,2026-10-20T10:00:00+03:00\n"
    )
    got = events(clean, manual, date(2026, 11, 1), date(2026, 11, 30), as_of=NOW)
    assert [(e.day, e.title, e.time) for e in got] == [
        (date(2026, 11, 3), "TÜFE Ekim 2026", "11:00"),  # the later-announced time wins
        (date(2026, 11, 6), "Employment Situation", ""),
        (date(2026, 11, 12), "Consumer Price Index", ""),
    ]
    before = events(
        clean,
        manual,
        date(2026, 11, 1),
        date(2026, 11, 30),
        as_of=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert [(e.title, e.time) for e in before] == [
        ("TÜFE Ekim 2026", "10:00")
    ]  # FRED not fetched yet
    assert [
        e.country
        for e in events(
            clean, manual, date(2026, 11, 1), date(2026, 11, 30), as_of=NOW, countries={"TR"}
        )
    ] == ["TR"]

    manual.write_text(
        "day,event,title,time,country,category,source,known_time\n2026-11-03,x,X,,TR,c,s,\n"
    )
    with pytest.raises(CalendarError, match="known_time is required"):
        events(clean, manual, date(2026, 11, 1), date(2026, 11, 30), as_of=NOW)
