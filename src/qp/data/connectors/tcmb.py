"""TCMB daily indicative FX rates ("gösterge niteliğindeki döviz kurları").

Public XML archive, no key: ``https://www.tcmb.gov.tr/kurlar/YYYYMM/DDMMYYYY.xml``.
Rates are set at 15:30 Istanbul time on TCMB business days; there is no bulletin on
weekends, holidays or the half days before religious holidays (reference.calendars "TCMB").

Each rate is stored as published: ``value`` per ``unit`` units (JPY is quoted per 100).
``known_time`` = min(15:30 Istanbul on the bulletin date, fetched_at), basis "schedule".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from uuid import uuid4

import pyarrow as pa

from qp.core import QPError, assume_istanbul, ensure_utc, to_decimal
from qp.data.connectors.http import FetchError, HttpClient
from qp.data.lake import CleanStore, Dataset, RawStore, Row
from qp.platform.logging import get_logger
from qp.reference.calendars import sessions

log = get_logger("qp.data.tcmb")

SOURCE = "tcmb"
RAW_DATASET = "fx_daily"
PUBLISH_TIME = time(15, 30)  # Istanbul
BASE_URL = "https://www.tcmb.gov.tr/kurlar"
DATASET = Dataset(
    name="tcmb_fx_daily",
    keys=("currency", "rate_kind"),
    values=("unit", "value"),
    fields=(
        pa.field("currency", pa.string(), nullable=False),
        pa.field("rate_kind", pa.string(), nullable=False),
        pa.field("valid_date", pa.date32(), nullable=False),
        pa.field("unit", pa.int32(), nullable=False),
        pa.field("value", pa.decimal128(38, 18), nullable=False),
    ),
)
_KINDS = {
    "ForexBuying": "forex_buying",
    "ForexSelling": "forex_selling",
    "BanknoteBuying": "banknote_buying",
    "BanknoteSelling": "banknote_selling",
}


class TcmbParseError(QPError):
    pass


def archive_url(day: date) -> str:
    return f"{BASE_URL}/{day:%Y%m}/{day:%d%m%Y}.xml"


def scheduled_known_time(day: date) -> datetime:
    return ensure_utc(assume_istanbul(datetime.combine(day, PUBLISH_TIME)))


@dataclass(frozen=True, slots=True)
class Rate:
    currency: str
    kind: str
    unit: int
    value: Decimal


@dataclass(frozen=True)
class Bulletin:
    valid_date: date
    number: str
    rates: list[Rate]


def parse(payload: bytes) -> Bulletin:
    """Parse one bulletin. Empty fields (e.g. XDR banknote rates) are skipped, not zeroed."""
    try:
        # A TCMB payload; Python's expat refuses entity-expansion bombs, and no external
        # entities are resolved.
        root = ET.fromstring(payload)  # noqa: S314
    except ET.ParseError as exc:
        raise TcmbParseError(f"not XML: {exc}") from exc
    if root.tag != "Tarih_Date" or not root.get("Tarih"):
        raise TcmbParseError(f"unexpected root {root.tag!r}")
    try:
        day = datetime.strptime(root.get("Tarih", ""), "%d.%m.%Y").date()  # noqa: DTZ007
    except ValueError as exc:
        raise TcmbParseError(f"bad Tarih {root.get('Tarih')!r}") from exc
    rates: list[Rate] = []
    for cur in root.findall("Currency"):
        code = (cur.get("CurrencyCode") or "").strip()
        unit_text = (cur.findtext("Unit") or "").strip()
        if len(code) != 3 or not unit_text.isdigit() or int(unit_text) <= 0:
            raise TcmbParseError(f"bad currency entry {code!r} unit {unit_text!r}")
        for tag, kind in _KINDS.items():
            text = (cur.findtext(tag) or "").strip()
            if not text:
                continue
            value = to_decimal(text)
            if value <= 0:
                raise TcmbParseError(f"{code} {tag} is not positive: {text}")
            rates.append(Rate(code, kind, int(unit_text), value))
    if not rates:
        raise TcmbParseError("bulletin has no rates")
    return Bulletin(day, root.get("Bulten_No", ""), rates)


def to_rows(bulletin: Bulletin, *, raw_ref: str, fetched_at: datetime) -> list[Row]:
    fetched_at = ensure_utc(fetched_at)
    known = min(scheduled_known_time(bulletin.valid_date), fetched_at)
    return [
        {
            "currency": r.currency,
            "rate_kind": r.kind,
            "valid_date": bulletin.valid_date,
            "unit": r.unit,
            "value": r.value,
            "source": SOURCE,
            "known_time": known,
            "known_time_basis": "schedule",
            "fetched_at": fetched_at,
            "raw_ref": raw_ref,
        }
        for r in bulletin.rates
    ]


@dataclass
class FetchReport:
    source: str
    requested: list[date] = field(default_factory=list)
    fetched: int = 0
    rows_written: int = 0
    missing: list[date] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def fetch_range(
    client: HttpClient,
    raw: RawStore,
    clean: CleanStore,
    start: date,
    end: date,
    *,
    now: Callable[[], datetime],
) -> FetchReport:
    """Fetch every TCMB business day in [start, end] whose 15:30 publication has passed."""
    report = FetchReport(SOURCE)
    for day in sessions("TCMB", start, end):
        if scheduled_known_time(day) > ensure_utc(now()):
            break  # not published yet
        report.requested.append(day)
        url = archive_url(day)
        try:
            res = client.get(url)
        except FetchError as exc:
            report.errors.append(f"{day}: {exc}")
            continue
        fetched_at = ensure_utc(now())
        if res.status == 404:
            report.missing.append(day)
            continue
        if res.status != 200:
            report.errors.append(f"{day}: HTTP {res.status}")
            continue
        rec = raw.write(
            SOURCE,
            RAW_DATASET,
            f"{day:%Y%m%d}-{fetched_at:%Y%m%dt%H%M%S%fz}-{uuid4().hex[:6]}.xml",
            res.content,
            fetched_at=fetched_at,
            meta={"url": res.safe_url, "status": res.status},
        )
        report.fetched += 1
        try:
            bulletin = parse(res.content)
        except (TcmbParseError, QPError) as exc:
            report.errors.append(f"{day}: {exc} (raw kept at {rec.ref})")
            continue
        if bulletin.valid_date != day:
            report.errors.append(f"{day}: bulletin is dated {bulletin.valid_date}")
            continue
        report.rows_written += clean.append(
            DATASET, to_rows(bulletin, raw_ref=rec.ref, fetched_at=fetched_at)
        )
    log.info(
        "tcmb.fetch",
        start=str(start),
        end=str(end),
        requested=len(report.requested),
        fetched=report.fetched,
        rows=report.rows_written,
        missing=[str(d) for d in report.missing],
        errors=len(report.errors),
    )
    return report
