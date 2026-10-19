from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from qp.data.connectors import tcmb
from qp.data.connectors.http import HttpClient
from qp.data.lake import CleanStore, RawStore
from qp.data.quality.checks import (
    QualityStatus,
    jumps,
    known_before_valid,
    missing_sessions,
    not_positive,
    stale,
)
from qp.data.quality.runner import check_tcmb_fx, last_tcmb_bulletin_due
from qp.platform.lake import LakePaths

D = date(2026, 10, 12)
FIXTURE = Path(__file__).parents[2] / "fixtures" / "tcmb" / "bulletin.xml"


def r(day: int, value: str, known: datetime | None = None) -> dict[str, Any]:
    d = D + timedelta(days=day)
    return {
        "valid_date": d,
        "value": Decimal(value),
        "known_time": known or datetime.combine(d, datetime.min.time(), UTC) + timedelta(hours=13),
    }


def test_individual_checks() -> None:
    rows = [r(0, "10"), r(1, "10.5"), r(3, "12")]
    expected = [D + timedelta(days=i) for i in range(4)]
    miss = missing_sessions("ds", "s", rows, expected)
    assert miss.status is QualityStatus.WARN
    assert "2026-10-14" in miss.detail
    assert missing_sessions("ds", "s", rows, expected[:2]).status is QualityStatus.OK
    assert stale("ds", "s", rows, D + timedelta(days=3)).status is QualityStatus.OK
    assert stale("ds", "s", rows, D + timedelta(days=4)).status is QualityStatus.WARN
    assert stale("ds", "s", [], D).status is QualityStatus.FAIL
    assert not_positive("ds", "s", [*rows, r(4, "0")]).status is QualityStatus.FAIL
    value = lambda x: x["value"]  # noqa: E731
    jump = jumps("ds", "s", rows, threshold=Decimal("0.10"), value=value)
    assert jump.status is QualityStatus.WARN
    assert "+14.29%" in jump.detail  # 10.5 -> 12
    assert jumps("ds", "s", rows, threshold=Decimal("0.2"), value=value).status is QualityStatus.OK
    # 23:59 Istanbul on the day before
    early = r(0, "1", known=datetime(2026, 10, 11, 20, 59, tzinfo=UTC))
    assert known_before_valid("ds", "s", [early]).status is QualityStatus.FAIL
    ok = r(0, "1", known=datetime(2026, 10, 11, 21, 0, tzinfo=UTC))  # 00:00 Istanbul
    assert known_before_valid("ds", "s", [ok]).status is QualityStatus.OK


def test_last_bulletin_due() -> None:
    assert last_tcmb_bulletin_due(datetime(2026, 10, 16, 12, 29, tzinfo=UTC)) == date(2026, 10, 15)
    assert last_tcmb_bulletin_due(datetime(2026, 10, 16, 12, 30, tzinfo=UTC)) == date(2026, 10, 16)
    assert last_tcmb_bulletin_due(datetime(2026, 10, 18, 9, tzinfo=UTC)) == date(2026, 10, 16)


def test_check_tcmb_fx_over_fetched_lake(tmp_path: Path) -> None:
    paths = LakePaths(tmp_path)
    raw, clean = RawStore(paths), CleanStore(paths)

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1].removesuffix(".xml")
        if name == "14102026":
            return httpx.Response(404)
        day = f"{name[:2]}.{name[2:4]}.{name[4:]}"
        return httpx.Response(
            200, content=FIXTURE.read_bytes().replace(b"16.10.2026", day.encode())
        )

    now = datetime(2026, 10, 19, 13, tzinfo=UTC)
    client = HttpClient(
        user_agent="t",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )
    with client:
        tcmb.fetch_range(client, raw, clean, D, date(2026, 10, 19), now=lambda: now)
    results = {(c.subject, c.check): c for c in check_tcmb_fx(clean, start=D, now=now)}
    assert results[("USD forex_buying", "missing_sessions")].status is QualityStatus.WARN
    assert "2026-10-14" in results[("USD forex_buying", "missing_sessions")].detail
    assert results[("USD forex_buying", "stale")].status is QualityStatus.OK
    assert results[("USD forex_buying", "not_positive")].status is QualityStatus.OK
    assert results[("USD forex_buying", "known_before_valid")].status is QualityStatus.OK
    assert results[("EUR forex_buying", "stale")].status is QualityStatus.FAIL  # not in fixture
