"""SEC company facts connector and point-in-time fundamentals (ADR 0013, 8-9), synthetic."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from qp.data.connectors import sec
from qp.data.connectors.http import HttpClient
from qp.data.lake import CleanStore, RawStore
from qp.fundamentals.facts import REVENUE, Value, facts, snapshot, ticker_ciks, ttm
from qp.platform.lake import LakePaths

REV = REVENUE[0]


def fact(
    start: str | None, end: str, val: int, filed: str, accn: str, form: str, fp: str
) -> dict[str, object]:
    out: dict[str, object] = {
        "end": end,
        "val": val,
        "accn": accn,
        "fy": int(end[:4]),
        "fp": fp,
        "form": form,
        "filed": filed,
    }
    if start:
        out["start"] = start
    return out


COMPANY = {
    "cik": 1,
    "entityName": "TESTCO",
    "facts": {
        "us-gaap": {
            REV: {
                "units": {
                    "USD": [
                        fact("2024-01-01", "2024-03-31", 250, "2024-05-01", "a-24q1", "10-Q", "Q1"),
                        fact("2024-01-01", "2024-12-31", 1000, "2025-02-15", "a-24k", "10-K", "FY"),
                        fact("2024-01-01", "2024-03-31", 250, "2025-05-01", "a-25q1", "10-Q", "Q1"),
                        fact("2025-01-01", "2025-03-31", 300, "2025-05-01", "a-25q1", "10-Q", "Q1"),
                        fact(
                            "2024-01-01", "2024-12-31", 1010, "2025-06-01", "a-24ka", "10-K/A", "FY"
                        ),
                    ]
                }
            },
            "StockholdersEquity": {
                "units": {
                    "USD": [
                        fact(None, "2024-12-31", 500, "2025-02-15", "a-24k", "10-K", "FY"),
                        fact(None, "2025-03-31", 520, "2025-05-01", "a-25q1", "10-Q", "Q1"),
                        fact(None, "2025-03-31", 525, "2025-05-01", "a-25q1b", "10-Q", "Q1"),
                    ]
                }
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [fact(None, "2025-04-25", 100, "2025-05-01", "a-25q1", "10-Q", "Q1")]
                }
            }
        },
    },
}
TICKERS = {"0": {"cik_str": 1, "ticker": "TST", "title": "TESTCO"}}


def at(day: str) -> datetime:
    return datetime.fromisoformat(day + "T12:00:00+00:00")


def test_fetch_and_point_in_time_fundamentals(tmp_path: Path) -> None:
    paths = LakePaths(tmp_path / "lake")
    raw, clean = RawStore(paths), CleanStore(paths)
    agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["User-Agent"])
        body = TICKERS if "company_tickers" in str(request.url) else COMPANY
        return httpx.Response(200, content=json.dumps(body).encode())

    client = HttpClient(
        user_agent="qp test contact@example.org",
        min_interval_s=0,
        retries=0,
        transport=httpx.MockTransport(handler),
    )
    with client:
        report = sec.fetch_companies(
            client, raw, clean, ["TST", "NOPE"], now=lambda: datetime(2026, 1, 1, tzinfo=UTC)
        )
    assert report.errors == ["NOPE: not in SEC's ticker file"]
    assert set(agents) == {"qp test contact@example.org"}
    # 9 facts in the payload: of the two equity values filed the same day the later
    # accession wins (8), and the Q1 2024 value repeated in a later filing is not new (7).
    assert report.rows_written == 7
    assert ticker_ciks(tmp_path / "lake") == {"TST": "0000000001"}

    def rev(day: str) -> Value | None:
        return ttm(facts(clean, "1", REV, as_of=at(day)))

    v = rev("2025-04-01")  # FY 2024 only (Q1 2025 not filed yet)
    assert v is not None
    assert (v.value, v.end) == (Decimal(1000), date(2024, 12, 31))
    v = rev("2025-05-10")  # 1000 + 300 - 250
    assert v is not None
    assert (v.value, v.end) == (Decimal(1050), date(2025, 3, 31))
    v = rev("2025-07-01")  # restated FY: 1010 + 300 - 250
    assert v is not None
    assert v.value == Decimal(1060)
    assert rev("2024-01-01") is None

    s = snapshot(clean, "1", as_of=at("2025-05-10"))
    assert s.revenue is not None
    assert s.revenue.value == Decimal(1050)
    assert s.equity is not None
    assert s.equity.value == Decimal(525)
    assert s.shares is not None
    assert s.shares.value == Decimal(100)
    assert s.net_income is None
    assert any("net_income" in n for n in s.notes)
    early = snapshot(clean, "1", as_of=at("2025-03-01"))
    assert early.equity is not None
    assert early.equity.value == Decimal(500)
    assert early.shares is None
