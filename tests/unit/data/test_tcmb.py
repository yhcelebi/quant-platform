"""TCMB connector: parsing, scheduled known_time, fetching with a fake transport (no network)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from qp.core import EUR, TRY, USD, Currency, InstrumentId
from qp.data.connectors import tcmb
from qp.data.connectors.http import FetchError, HttpClient
from qp.data.lake import CleanStore, RawStore
from qp.data.lake_market import LakeMarketData, plain
from qp.data.manual import ManualMarketData, default_known_time
from qp.data.market import FxPoint, LayeredMarketData, PricePoint
from qp.platform.lake import LakePaths

JPY = Currency("JPY")
FIXTURE = Path(__file__).parents[2] / "fixtures" / "tcmb" / "bulletin.xml"
DAY = date(2026, 10, 16)  # a Friday; the fixture's Tarih
PUBLISHED = datetime(2026, 10, 16, 12, 30, tzinfo=UTC)  # 15:30 Istanbul


def payload(day: date = DAY) -> bytes:
    return FIXTURE.read_bytes().replace(b"16.10.2026", f"{day:%d.%m.%Y}".encode())


# ---------------------------------------------------------------- parsing
def test_parse_fixture() -> None:
    b = tcmb.parse(payload())
    assert b.valid_date == DAY
    got = {(r.currency, r.kind): (r.unit, r.value) for r in b.rates}
    assert got[("USD", "forex_buying")] == (1, Decimal("40.1000"))
    assert got[("JPY", "forex_selling")] == (100, Decimal("27.7000"))
    assert ("XDR", "forex_buying") in got
    assert ("XDR", "banknote_buying") not in got  # empty fields are skipped, never zero
    assert len(b.rates) == 4 + 4 + 1


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not xml", "not XML"),
        (b"<Other/>", "unexpected root"),
        (b'<Tarih_Date Tarih="2026-10-16"/>', "bad Tarih"),
        (b'<Tarih_Date Tarih="16.10.2026"/>', "no rates"),
        (
            b'<Tarih_Date Tarih="16.10.2026"><Currency CurrencyCode="USD"><Unit>0</Unit>'
            b"</Currency></Tarih_Date>",
            "bad currency entry",
        ),
        (
            b'<Tarih_Date Tarih="16.10.2026"><Currency CurrencyCode="USD"><Unit>1</Unit>'
            b"<ForexBuying>-1</ForexBuying></Currency></Tarih_Date>",
            "not positive",
        ),
    ],
)
def test_parse_rejects_malformed(data: bytes, message: str) -> None:
    with pytest.raises(tcmb.TcmbParseError, match=message):
        tcmb.parse(data)


def test_known_time_is_publication_capped_at_fetch() -> None:
    b = tcmb.parse(payload())
    late = tcmb.to_rows(b, raw_ref="r", fetched_at=PUBLISHED + timedelta(days=400))
    assert {r["known_time"] for r in late} == {PUBLISHED}  # backfill: published that day
    early = tcmb.to_rows(b, raw_ref="r", fetched_at=PUBLISHED - timedelta(minutes=5))
    assert {r["known_time"] for r in early} == {PUBLISHED - timedelta(minutes=5)}


# ---------------------------------------------------------------- fetching
class FakeTcmb:
    """Serves the fixture for business days; 404 otherwise or when told to."""

    def __init__(self, missing: set[date] | None = None) -> None:
        self.missing = missing or set()
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        name = request.url.path.rsplit("/", 1)[-1].removesuffix(".xml")
        day = datetime.strptime(name, "%d%m%Y").date()  # noqa: DTZ007
        if day in self.missing:
            return httpx.Response(404)
        return httpx.Response(200, content=payload(day))


def client(handler: Callable[[httpx.Request], httpx.Response]) -> HttpClient:
    return HttpClient(
        user_agent="test",
        min_interval_s=0,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


@pytest.fixture
def lake(tmp_path: Path) -> tuple[RawStore, CleanStore]:
    paths = LakePaths(tmp_path)
    return RawStore(paths), CleanStore(paths)


def test_fetch_range_skips_non_business_days_and_unpublished(
    lake: tuple[RawStore, CleanStore],
) -> None:
    raw, clean = lake
    fake = FakeTcmb(missing={date(2026, 10, 14)})
    now = datetime(2026, 10, 19, 10, tzinfo=UTC)  # Monday 13:00 Istanbul: not yet published
    with client(fake) as c:
        report = tcmb.fetch_range(
            c, raw, clean, date(2026, 10, 9), date(2026, 10, 19), now=lambda: now
        )
    # Fri 9, Mon 12 - Fri 16 (weekend skipped); Mon 19 is before 15:30
    assert report.requested == [date(2026, 10, 9), *(date(2026, 10, d) for d in range(12, 17))]
    assert report.missing == [date(2026, 10, 14)]
    assert report.fetched == 5
    assert report.rows_written == 5 * 9
    assert report.ok
    assert all("kurlar/202610/" in u for u in fake.urls)
    with client(fake) as c:  # refetch: raw keeps a new copy, clean gets nothing new
        again = tcmb.fetch_range(
            c, raw, clean, date(2026, 10, 9), date(2026, 10, 16), now=lambda: now
        )
    assert again.rows_written == 0


def test_fetch_reports_bad_payload_and_keeps_raw(lake: tuple[RawStore, CleanStore]) -> None:
    raw, clean = lake
    now = datetime(2026, 10, 17, tzinfo=UTC)
    with client(lambda _: httpx.Response(200, content=b"<html>maintenance</html>")) as c:
        report = tcmb.fetch_range(c, raw, clean, DAY, DAY, now=lambda: now)
    assert not report.ok
    assert "unexpected root" in report.errors[0]
    assert report.fetched == 1  # stored in raw for inspection
    with client(lambda _: httpx.Response(200, content=payload(DAY - timedelta(days=1)))) as c:
        report = tcmb.fetch_range(c, raw, clean, DAY, DAY, now=lambda: now)
    assert "dated 2026-10-15" in report.errors[0]


# ---------------------------------------------------------------- market data
def test_lake_market_data_fx_per_unit_and_point_in_time(lake: tuple[RawStore, CleanStore]) -> None:
    raw, clean = lake
    now = datetime(2026, 10, 20, tzinfo=UTC)
    with client(FakeTcmb()) as c:
        tcmb.fetch_range(c, raw, clean, DAY, DAY, now=lambda: now)
    md = LakeMarketData(clean)
    usd = md.fx_try(USD, date(2026, 10, 18), as_of=now)  # Sunday: Friday's rate
    assert usd is not None
    assert (usd.valid_date, usd.rate_try, usd.source) == (DAY, Decimal("40.1"), "tcmb:forex_buying")
    jpy = md.fx_try(JPY, DAY, as_of=now)
    assert jpy is not None
    assert jpy.rate_try == Decimal("0.275")  # 27.5000 per 100 yen
    assert md.fx_try(USD, DAY, as_of=PUBLISHED - timedelta(seconds=1)) is None  # not yet known
    assert md.fx_try(EUR, DAY, as_of=now) is None
    selling = LakeMarketData(clean, rate_kind="forex_selling").fx_try(USD, DAY, as_of=now)
    assert selling is not None
    assert selling.rate_try == Decimal("40.2")
    assert md.price(InstrumentId.parse("THYAO.BIST"), DAY, as_of=now) is None


def test_layered_market_data_prefers_newest_then_manual() -> None:
    def fx(day: date, rate: str, src: str) -> FxPoint:
        return FxPoint(USD, day, Decimal(rate), src, default_known_time(day))

    manual = ManualMarketData([], [fx(DAY, "41", "manual")])
    fetched = ManualMarketData(
        [], [fx(DAY, "40", "tcmb"), fx(DAY + timedelta(days=3), "42", "tcmb")]
    )
    layered = LayeredMarketData([manual, fetched])
    as_of = datetime(2026, 12, 1, tzinfo=UTC)
    same_day = layered.fx_try(USD, DAY, as_of=as_of)
    newer = layered.fx_try(USD, DAY + timedelta(days=5), as_of=as_of)
    assert same_day is not None
    assert same_day.source == "manual"
    assert newer is not None
    assert newer.rate_try == Decimal("42")
    assert layered.fx_try(Currency("GBP"), DAY, as_of=as_of) is None
    thy = InstrumentId.parse("THYAO.BIST")
    p = PricePoint(thy, DAY, Decimal("300"), TRY, "manual", default_known_time(DAY))
    assert LayeredMarketData([ManualMarketData([p], []), fetched]).price(thy, DAY, as_of=as_of) == p


def test_plain_strips_storage_scale() -> None:
    assert str(plain(Decimal("40.100000000000000000"))) == "40.1"
    assert str(plain(Decimal("100.000000000000000000"))) == "100"
    assert str(plain(Decimal("0.275000000000000000"))) == "0.275"


# ---------------------------------------------------------------- http client
def test_http_retries_then_gives_up_and_rate_limits() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, headers={"Retry-After": "7"})
        return httpx.Response(200, content=b"ok")

    clock = iter(float(i) for i in range(100))
    c = HttpClient(
        user_agent="t",
        min_interval_s=1.5,
        transport=httpx.MockTransport(flaky),
        sleep=sleeps.append,
        monotonic=lambda: next(clock),
        backoff_s=1,
    )
    assert c.get("https://x.test/a").content == b"ok"
    assert len(calls) == 3
    assert 7.0 in sleeps  # Retry-After honoured

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    c2 = HttpClient(
        user_agent="t",
        min_interval_s=0,
        transport=httpx.MockTransport(down),
        sleep=lambda _: None,
        retries=1,
    )
    with pytest.raises(FetchError, match="GET https://safe failed: ConnectTimeout"):
        c2.get("https://x.test/?api_key=SECRET", safe_url="https://safe")
