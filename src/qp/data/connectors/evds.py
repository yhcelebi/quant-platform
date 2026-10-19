"""TCMB EVDS 3 web service (macro and financial series).

From TCMB's EVDS 3 web-service guide (evds3.tcmb.gov.tr, documents docId=16, read
2026-10-19):

- ``GET {base}series=CODE&startDate=dd-mm-yyyy&endDate=dd-mm-yyyy&type=json``: parameters
  are part of the path, not a ``?`` query; the API key goes in the ``key`` request header
  (403 without it).
- One request returns at most 1000 observations, silently dropping older ones; startDate
  must be the first day of a period. So: one series per request, one calendar year per
  request (<= 366 observations, and Jan 1 starts every daily/monthly/quarterly/annual period).
- JSON ``{"items": [{"Tarih": ..., "TP_FG_J0": "513.30000000", ...}]}``; the series column
  is the code with "." -> "_"; values are strings or null. ``Tarih`` is dd-mm-yyyy (daily,
  weekly), YYYY-M (monthly), YYYY-Qn (quarterly) or YYYY (annual). The JSON shape comes from
  third-party clients, not TCMB's guide: verify on the first run with a real key.

EVDS keeps no vintages: ``known_time`` = fetched_at (basis "fetched"); a later fetch with a
different value is a revision. ``valid_date`` is the first day of the period.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime
from uuid import uuid4

import pyarrow as pa

from qp.core import QPError, ensure_utc, to_decimal
from qp.data.connectors.http import FetchError, HttpClient
from qp.data.connectors.tcmb import FetchReport
from qp.data.lake import CleanStore, Dataset, RawStore, Row
from qp.platform.logging import get_logger

log = get_logger("qp.data.evds")

SOURCE = "evds"
RAW_DATASET = "series"
DATASET = Dataset(
    name="evds_observations",
    keys=("series_code",),
    values=("value",),
    fields=(
        pa.field("series_code", pa.string(), nullable=False),
        pa.field("valid_date", pa.date32(), nullable=False),
        pa.field("period", pa.string(), nullable=False),
        pa.field("value", pa.decimal128(38, 18), nullable=False),
    ),
)
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_.]*$")
_DAILY = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_MONTHLY = re.compile(r"^(\d{4})-(\d{1,2})$")
_QUARTERLY = re.compile(r"^(\d{4})-Q([1-4])$")
_ANNUAL = re.compile(r"^(\d{4})$")


class EvdsError(QPError):
    pass


def period_start(tarih: str) -> date:
    if m := _DAILY.fullmatch(tarih):
        return date(int(m[3]), int(m[2]), int(m[1]))
    if m := _MONTHLY.fullmatch(tarih):
        return date(int(m[1]), int(m[2]), 1)
    if m := _QUARTERLY.fullmatch(tarih):
        return date(int(m[1]), 3 * (int(m[2]) - 1) + 1, 1)
    if m := _ANNUAL.fullmatch(tarih):
        return date(int(m[1]), 1, 1)
    raise EvdsError(f"unknown Tarih format {tarih!r}")


def request_url(base: str, code: str, start: date, end: date) -> str:
    if not _CODE.fullmatch(code):
        raise EvdsError(f"invalid series code {code!r}")
    return f"{base}series={code}&startDate={start:%d-%m-%Y}&endDate={end:%d-%m-%Y}&type=json"


def parse(payload: bytes, code: str) -> list[dict[str, object]]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise EvdsError(f"not JSON: {exc}") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise EvdsError("no 'items' in response")
    column = code.replace(".", "_")
    out: list[dict[str, object]] = []
    for item in items:
        if column not in item or "Tarih" not in item:
            raise EvdsError(f"item without Tarih/{column}: {sorted(item)}")
        value = item[column]
        if value is None or value == "":
            continue
        out.append(
            {
                "series_code": code,
                "valid_date": period_start(str(item["Tarih"])),
                "period": str(item["Tarih"]),
                "value": to_decimal(str(value)),
            }
        )
    return out


def to_rows(obs: Sequence[dict[str, object]], *, raw_ref: str, fetched_at: datetime) -> list[Row]:
    fetched_at = ensure_utc(fetched_at)
    return [
        {
            **o,
            "source": SOURCE,
            "known_time": fetched_at,
            "known_time_basis": "fetched",
            "fetched_at": fetched_at,
            "raw_ref": raw_ref,
        }
        for o in obs
    ]


def year_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Calendar-year windows covering [start, end], each starting on Jan 1 (period-aligned)."""
    return [(date(y, 1, 1), min(date(y, 12, 31), end)) for y in range(start.year, end.year + 1)]


def fetch_series(
    client: HttpClient,
    raw: RawStore,
    clean: CleanStore,
    series: Sequence[str],
    start: date,
    end: date,
    *,
    base_url: str,
    api_key: str,
    now: Callable[[], datetime],
) -> FetchReport:
    report = FetchReport(SOURCE)
    for code in series:
        for w_start, w_end in year_windows(start, end):
            url = request_url(base_url, code, w_start, w_end)
            try:
                res = client.get(url, headers={"key": api_key})
            except FetchError as exc:
                report.errors.append(f"{code} {w_start.year}: {exc}")
                break
            if res.status in (401, 403):
                report.errors.append(f"{code}: HTTP {res.status} (missing or wrong API key)")
                return report
            fetched_at = ensure_utc(now())
            rec = raw.write(
                SOURCE,
                RAW_DATASET,
                f"{code.lower().replace('_', '-')}-{w_start.year}-"
                f"{fetched_at:%Y%m%dt%H%M%S%fz}-{uuid4().hex[:6]}.json",
                res.content,
                fetched_at=fetched_at,
                meta={"url": res.safe_url, "status": res.status},
            )
            report.fetched += 1
            if res.status != 200:
                report.errors.append(f"{code} {w_start.year}: HTTP {res.status} (raw {rec.ref})")
                break
            try:
                obs = parse(res.content, code)
            except (EvdsError, QPError) as exc:
                report.errors.append(f"{code} {w_start.year}: {exc} (raw {rec.ref})")
                break
            if len(obs) >= 1000:
                report.errors.append(f"{code} {w_start.year}: 1000+ rows, may be truncated")
            report.rows_written += clean.append(
                DATASET, to_rows(obs, raw_ref=rec.ref, fetched_at=fetched_at)
            )
    log.info("evds.fetch", series=list(series), rows=report.rows_written, errors=len(report.errors))
    return report


def series_info(
    client: HttpClient, *, base_url: str, api_key: str, code: str
) -> list[dict[str, object]]:
    """Metadata for a series or data group code (``serieList``): names, frequency, dates.

    Used to confirm codes before adding them to ``sources.evds.series``.
    """
    if not _CODE.fullmatch(code) and not re.fullmatch(r"^bie_[a-z0-9]+$", code):
        raise EvdsError(f"invalid code {code!r}")
    res = client.get(f"{base_url}serieList/type=json&code={code}", headers={"key": api_key})
    if res.status in (401, 403):
        raise EvdsError(f"HTTP {res.status} (missing or wrong API key)")
    if res.status != 200:
        raise EvdsError(f"HTTP {res.status}")
    try:
        data = json.loads(res.content)
    except json.JSONDecodeError as exc:
        raise EvdsError(f"not JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("items", [data])
    if not isinstance(data, list):
        raise EvdsError("unexpected serieList response")
    return [d for d in data if isinstance(d, dict)]
