"""SEC EDGAR XBRL company facts, point in time by filing date (ADR 0013, 8).

- ``https://www.sec.gov/files/company_tickers.json``: ticker -> CIK.
- ``https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json``: every fact a company
  reported (``units`` -> list of ``{start?, end, val, accn, fy, fp, form, filed, frame?}``;
  structure checked against a live response on 2026-10-25).

Lake dataset ``sec_facts``: keys (cik, taxonomy, concept, unit, start) where ``start`` is ""
for instants; valid date = ``end``; compared value ``val``; ``accn, form, fy, fp`` describe
the first filing that reported the value. ``known_time`` = end of the ``filed`` day in
America/New_York (only the date is given; never earlier than the filing), basis ``source``,
so a restated value in a later filing is a revision known from that filing on.
Two values for one fact filed the same day: the later accession number wins.

SEC fair access: a declared User-Agent with a contact (owner-set), at most 10 requests/s.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa

from qp.core import QPError, ensure_utc, to_decimal
from qp.data.connectors.http import FetchError, HttpClient
from qp.data.connectors.tcmb import FetchReport
from qp.data.lake import CleanStore, Dataset, RawStore, Row
from qp.platform.logging import get_logger

log = get_logger("qp.data.sec")

SOURCE = "sec"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_NEW_YORK = ZoneInfo("America/New_York")
DATASET = Dataset(
    name="sec_facts",
    keys=("cik", "taxonomy", "concept", "unit", "start"),
    values=("val",),
    fields=(
        pa.field("cik", pa.string(), nullable=False),
        pa.field("taxonomy", pa.string(), nullable=False),
        pa.field("concept", pa.string(), nullable=False),
        pa.field("unit", pa.string(), nullable=False),
        pa.field("start", pa.string(), nullable=False),
        pa.field("valid_date", pa.date32(), nullable=False),
        pa.field("val", pa.decimal128(38, 6), nullable=False),
        pa.field("accn", pa.string(), nullable=False),
        pa.field("form", pa.string(), nullable=False),
        pa.field("fy", pa.int32(), nullable=True),
        pa.field("fp", pa.string(), nullable=False),
    ),
)


class SecError(QPError):
    pass


def cik10(cik: int | str) -> str:
    return f"{int(cik):010d}"


def filed_known_time(filed: date) -> datetime:
    return ensure_utc(datetime.combine(filed, time(23, 59, 59), tzinfo=_NEW_YORK))


def parse_tickers(payload: bytes) -> dict[str, str]:
    """Upper-case ticker -> 10-digit CIK."""
    try:
        data = json.loads(payload)
        return {str(v["ticker"]).upper(): cik10(v["cik_str"]) for v in data.values()}
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise SecError(f"unexpected company_tickers payload: {exc}") from exc


def parse_facts(payload: bytes) -> tuple[str, str, list[dict[str, Any]]]:
    """(cik, entity name, fact rows without point-in-time columns)."""
    try:
        data = json.loads(payload)
        cik = cik10(data["cik"])
        name = str(data.get("entityName", ""))
        facts: list[dict[str, Any]] = []
        for taxonomy, concepts in data["facts"].items():
            for concept, body in concepts.items():
                for unit, items in body["units"].items():
                    for f in items:
                        facts.append(
                            {
                                "cik": cik,
                                "taxonomy": taxonomy,
                                "concept": concept,
                                "unit": unit,
                                "start": str(f.get("start", "")),
                                "valid_date": date.fromisoformat(f["end"]),
                                "val": to_decimal(str(f["val"])),
                                "accn": str(f["accn"]),
                                "form": str(f.get("form", "")),
                                "fy": int(f["fy"]) if f.get("fy") is not None else None,
                                "fp": str(f.get("fp") or ""),
                                "filed": date.fromisoformat(f["filed"]),
                            }
                        )
    except (ValueError, KeyError, TypeError, AttributeError, QPError) as exc:
        raise SecError(f"unexpected companyfacts payload: {exc}") from exc
    return cik, name, facts


def to_rows(facts: Sequence[dict[str, Any]], *, raw_ref: str, fetched_at: datetime) -> list[Row]:
    """Point-in-time rows, ascending by filing; same-day duplicates resolved by accession."""
    fetched_at = ensure_utc(fetched_at)
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for f in facts:
        key = (
            f["cik"],
            f["taxonomy"],
            f["concept"],
            f["unit"],
            f["start"],
            f["valid_date"],
            f["filed"],
        )
        if key not in best or f["accn"] > best[key]["accn"]:
            best[key] = f
    rows: list[Row] = []
    for f in sorted(best.values(), key=lambda x: (x["filed"], x["accn"])):
        known = min(filed_known_time(f["filed"]), fetched_at)
        row = {k: v for k, v in f.items() if k != "filed"}
        rows.append(
            {
                **row,
                "val": Decimal(row["val"]),
                "source": SOURCE,
                "known_time": known,
                "known_time_basis": "source",
                "fetched_at": fetched_at,
                "raw_ref": raw_ref,
            }
        )
    return rows


def fetch_companies(
    client: HttpClient,
    raw: RawStore,
    clean: CleanStore,
    tickers: Sequence[str],
    *,
    now: Callable[[], datetime],
) -> FetchReport:
    """Company facts of each ticker (CIK from SEC's ticker file) into ``sec_facts``."""
    report = FetchReport(SOURCE)
    try:
        res = client.get(TICKERS_URL)
    except FetchError as exc:
        report.errors.append(f"tickers: {exc}")
        return report
    fetched_at = ensure_utc(now())
    rec = raw.write(
        SOURCE,
        "tickers",
        f"tickers-{fetched_at:%Y%m%dt%H%M%S%fz}-{uuid4().hex[:6]}.json",
        res.content,
        fetched_at=fetched_at,
        meta={"url": res.safe_url, "status": res.status},
    )
    report.fetched += 1
    if res.status != 200:
        report.errors.append(f"tickers: HTTP {res.status} (raw {rec.ref})")
        return report
    try:
        ciks = parse_tickers(res.content)
    except SecError as exc:
        report.errors.append(str(exc))
        return report
    for ticker in tickers:
        cik = ciks.get(ticker.upper())
        if cik is None:
            report.errors.append(f"{ticker}: not in SEC's ticker file")
            continue
        try:
            res = client.get(FACTS_URL.format(cik=cik))
        except FetchError as exc:
            report.errors.append(f"{ticker}: {exc}")
            continue
        fetched_at = ensure_utc(now())
        rec = raw.write(
            SOURCE,
            "companyfacts",
            f"cik{cik}-{fetched_at:%Y%m%dt%H%M%S%fz}-{uuid4().hex[:6]}.json",
            res.content,
            fetched_at=fetched_at,
            meta={"url": res.safe_url, "status": res.status, "ticker": ticker},
        )
        report.fetched += 1
        if res.status != 200:
            report.errors.append(f"{ticker}: HTTP {res.status} (raw {rec.ref})")
            continue
        try:
            _, _, facts = parse_facts(res.content)
        except SecError as exc:
            report.errors.append(f"{ticker}: {exc} (raw {rec.ref})")
            continue
        report.rows_written += clean.append(
            DATASET, to_rows(facts, raw_ref=rec.ref, fetched_at=fetched_at)
        )
    log.info(
        "sec.fetch", tickers=list(tickers), rows=report.rows_written, errors=len(report.errors)
    )
    return report
