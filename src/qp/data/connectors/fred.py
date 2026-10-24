"""FRED / ALFRED observations with every vintage (St. Louis Fed API).

``GET https://api.stlouisfed.org/fred/series/observations`` with ``realtime_start=1776-07-04``
and ``realtime_end=9999-12-31`` returns each observation once per real-time period
(``realtime_start``..``realtime_end``): the ALFRED vintages. Verified against the API docs
(fred.stlouisfed.org/docs/api/fred/series_observations.html, 2026-10-19).

- ``valid_date`` = the observation ``date`` (FRED dates a period by its first day).
- ``known_time`` = end of ``realtime_start`` in America/Chicago (St. Louis), capped at
  fetched_at; basis "source". FRED gives only the date, so the end of that day is the
  conservative choice: never earlier than the actual release.
- Values "." (not available in that vintage) are skipped.
- ``realtime_end`` is not stored: it changes when a later vintage appears, and the vintage
  history is fully captured by ``known_time`` per revision.
- The API key is a query parameter: only the redacted URL is logged or stored.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import urlencode
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa

from qp.core import QPError, ensure_utc, to_decimal
from qp.data.calendar import DATASET as CALENDAR
from qp.data.connectors.http import FetchError, HttpClient
from qp.data.connectors.tcmb import FetchReport
from qp.data.lake import CleanStore, Dataset, RawStore, Row
from qp.platform.logging import get_logger

log = get_logger("qp.data.fred")

SOURCE = "fred"
RAW_DATASET = "observations"
BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
PAGE_LIMIT = 100_000  # the API maximum
_CHICAGO = ZoneInfo("America/Chicago")
DATASET = Dataset(
    name="fred_observations",
    keys=("series_id",),
    values=("value",),
    fields=(
        pa.field("series_id", pa.string(), nullable=False),
        pa.field("valid_date", pa.date32(), nullable=False),
        pa.field("value", pa.decimal128(38, 18), nullable=False),
    ),
)


class FredError(QPError):
    pass


def vintage_known_time(realtime_start: date) -> datetime:
    return ensure_utc(datetime.combine(realtime_start, time(23, 59, 59), tzinfo=_CHICAGO))


def request_url(series_id: str, api_key: str, offset: int) -> tuple[str, str]:
    """(url, url with the key redacted)."""
    params = {
        "series_id": series_id,
        "file_type": "json",
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "limit": str(PAGE_LIMIT),
        "offset": str(offset),
    }
    safe = f"{BASE_URL}?{urlencode({**params, 'api_key': '***'})}"
    return f"{BASE_URL}?{urlencode({**params, 'api_key': api_key})}", safe


def parse(payload: bytes, series_id: str) -> tuple[int, list[dict[str, object]]]:
    """(total count, observations). Raises on an API error body."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FredError(f"not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FredError("unexpected JSON")
    if "error_message" in data:
        raise FredError(f"FRED error {data.get('error_code')}: {data['error_message']}")
    obs = data.get("observations")
    if not isinstance(obs, list) or not isinstance(data.get("count"), int):
        raise FredError("no observations/count in response")
    out: list[dict[str, object]] = []
    for o in obs:
        if o.get("value") == ".":
            continue
        try:
            out.append(
                {
                    "series_id": series_id,
                    "valid_date": date.fromisoformat(o["date"]),
                    "value": to_decimal(o["value"]),
                    "realtime_start": date.fromisoformat(o["realtime_start"]),
                }
            )
        except (KeyError, ValueError, QPError) as exc:
            raise FredError(f"bad observation {o!r}: {exc}") from exc
    return int(data["count"]), out


def to_rows(obs: Sequence[dict[str, object]], *, raw_ref: str, fetched_at: datetime) -> list[Row]:
    fetched_at = ensure_utc(fetched_at)
    rows = []
    for o in obs:
        start = o["realtime_start"]
        assert isinstance(start, date)
        value = o["value"]
        assert isinstance(value, Decimal)
        rows.append(
            {
                "series_id": o["series_id"],
                "valid_date": o["valid_date"],
                "value": value,
                "source": SOURCE,
                "known_time": min(vintage_known_time(start), fetched_at),
                "known_time_basis": "source",
                "fetched_at": fetched_at,
                "raw_ref": raw_ref,
            }
        )
    return rows


def fetch_series(
    client: HttpClient,
    raw: RawStore,
    clean: CleanStore,
    series: Sequence[str],
    *,
    api_key: str,
    now: Callable[[], datetime],
) -> FetchReport:
    report = FetchReport(SOURCE)
    for series_id in series:
        offset, count = 0, None
        while count is None or offset < count:
            url, safe = request_url(series_id, api_key, offset)
            try:
                res = client.get(url, safe_url=safe)
            except FetchError as exc:
                report.errors.append(f"{series_id}: {exc}")
                break
            fetched_at = ensure_utc(now())
            rec = raw.write(
                SOURCE,
                RAW_DATASET,
                f"{series_id.lower()}-{offset}-{fetched_at:%Y%m%dt%H%M%S%fz}-{uuid4().hex[:6]}.json",
                res.content,
                fetched_at=fetched_at,
                meta={"url": res.safe_url, "status": res.status},
            )
            report.fetched += 1
            try:
                count, obs = parse(res.content, series_id)
            except FredError as exc:
                report.errors.append(f"{series_id}: HTTP {res.status}: {exc} (raw {rec.ref})")
                break
            if res.status != 200:
                report.errors.append(f"{series_id}: HTTP {res.status} (raw {rec.ref})")
                break
            report.rows_written += clean.append(
                DATASET, to_rows(obs, raw_ref=rec.ref, fetched_at=fetched_at)
            )
            page = json.loads(res.content).get("observations", [])
            if not page:
                break
            offset += len(page)
    log.info("fred.fetch", series=list(series), rows=report.rows_written, errors=len(report.errors))
    return report


# --------------------------------------------------------------------------- release dates
RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"


def release_dates_url(api_key: str, start: date, offset: int) -> tuple[str, str]:
    """``fred/releases/dates`` with future dates (``include_release_dates_with_no_data``),
    ascending, 1000 per page (docs read 2026-10-25)."""
    params = {
        "file_type": "json",
        "realtime_start": start.isoformat(),
        "include_release_dates_with_no_data": "true",
        "order_by": "release_date",
        "sort_order": "asc",
        "limit": "1000",
        "offset": str(offset),
    }
    safe = f"{RELEASE_DATES_URL}?{urlencode({**params, 'api_key': '***'})}"
    return f"{RELEASE_DATES_URL}?{urlencode({**params, 'api_key': api_key})}", safe


def parse_release_dates(payload: bytes) -> tuple[int, list[dict[str, object]]]:
    try:
        data = json.loads(payload)
        count = int(data["count"])
        items = [
            {
                "calendar": "fred",
                "event": f"release:{int(r['release_id'])}",
                "valid_date": date.fromisoformat(r["date"]),
                "title": str(r["release_name"]),
                "time": "",
                "country": "US",
                "category": "release",
            }
            for r in data["release_dates"]
        ]
    except (ValueError, KeyError, TypeError) as exc:
        raise FredError(f"unexpected release dates payload: {exc}") from exc
    return count, items


def fetch_release_dates(
    client: HttpClient,
    raw: RawStore,
    clean: CleanStore,
    *,
    start: date,
    api_key: str,
    now: Callable[[], datetime],
    max_pages: int = 20,
) -> FetchReport:
    """Release dates from ``start`` on into ``calendar_events`` (known_time = fetch time)."""
    report = FetchReport(SOURCE)
    offset, count, pages = 0, None, 0
    while (count is None or offset < count) and pages < max_pages:
        url, safe = release_dates_url(api_key, start, offset)
        try:
            res = client.get(url, safe_url=safe)
        except FetchError as exc:
            report.errors.append(f"release dates: {exc}")
            break
        fetched_at = ensure_utc(now())
        rec = raw.write(
            SOURCE,
            "release_dates",
            f"release-dates-{offset}-{fetched_at:%Y%m%dt%H%M%S%fz}-{uuid4().hex[:6]}.json",
            res.content,
            fetched_at=fetched_at,
            meta={"url": res.safe_url, "status": res.status},
        )
        report.fetched += 1
        pages += 1
        if res.status != 200:
            report.errors.append(f"release dates: HTTP {res.status} (raw {rec.ref})")
            break
        try:
            count, items = parse_release_dates(res.content)
        except FredError as exc:
            report.errors.append(f"{exc} (raw {rec.ref})")
            break
        rows = [
            {
                **i,
                "source": SOURCE,
                "known_time": fetched_at,
                "known_time_basis": "fetched",
                "fetched_at": fetched_at,
                "raw_ref": rec.ref,
            }
            for i in items
        ]
        report.rows_written += clean.append(CALENDAR, rows)
        if not items:
            break
        offset += len(items)
    log.info("fred.release_dates", rows=report.rows_written, errors=len(report.errors))
    return report
