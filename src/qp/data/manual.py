"""Manual price and FX tables maintained by the owner as CSV (phase 1 valuation).

``prices.csv``: ``valid_date,instrument,price,currency,source,known_time``
``fx.csv``:     ``valid_date,currency,rate_try,source,known_time``

``known_time`` is optional (ISO 8601 with offset). When empty it defaults to the end of
``valid_date`` in Europe/Istanbul (23:59:59), i.e. a closing value known that evening.
Several rows for the same date with different ``known_time`` are revisions; queries
return the latest one known at ``as_of``.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Callable, Iterator
from datetime import date, datetime, time
from pathlib import Path

from qp.core import (
    Currency,
    InstrumentId,
    QPError,
    QPValidationError,
    assume_istanbul,
    parse_utc,
    to_decimal,
)
from qp.data.market import FxPoint, PricePoint

PRICE_COLUMNS = ("valid_date", "instrument", "price", "currency", "source", "known_time")
FX_COLUMNS = ("valid_date", "currency", "rate_try", "source", "known_time")


class ManualDataError(QPError):
    pass


def default_known_time(valid_date: date) -> datetime:
    return assume_istanbul(datetime.combine(valid_date, time(23, 59, 59)))


def _rows(path: Path, columns: tuple[str, ...]) -> Iterator[tuple[int, dict[str, str]]]:
    if not path.is_file():
        return
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = set(reader.fieldnames or [])
        required = set(columns) - {"known_time"}
        if not required <= header or not header <= set(columns):
            raise ManualDataError(f"{path}: header must be {list(columns)} (known_time optional)")
        for line, raw in enumerate(reader, start=2):
            yield line, {k: (v or "").strip() for k, v in raw.items()}


def _load[T](path: Path, columns: tuple[str, ...], parse: Callable[[dict[str, str]], T]) -> list[T]:
    items: list[T] = []
    for line, raw in _rows(path, columns):
        try:
            items.append(parse(raw))
        except (QPError, ValueError) as exc:
            raise ManualDataError(f"{path}: line {line}: {exc}") from exc
    return items


def _price(raw: dict[str, str]) -> PricePoint:
    valid = date.fromisoformat(raw["valid_date"])
    price = to_decimal(raw["price"])
    if price < 0:
        raise QPValidationError("price must be >= 0")
    return PricePoint(
        InstrumentId.parse(raw["instrument"]),
        valid,
        price,
        Currency(raw["currency"]),
        raw.get("source") or "manual",
        _known(raw, valid),
    )


def _fx(raw: dict[str, str]) -> FxPoint:
    valid = date.fromisoformat(raw["valid_date"])
    rate = to_decimal(raw["rate_try"])
    if rate <= 0:
        raise QPValidationError("rate_try must be positive")
    return FxPoint(
        Currency(raw["currency"]), valid, rate, raw.get("source") or "manual", _known(raw, valid)
    )


def _known(raw: dict[str, str], valid: date) -> datetime:
    text = raw.get("known_time", "")
    return parse_utc(text) if text else default_known_time(valid)


class ManualMarketData:
    def __init__(self, prices: list[PricePoint], fx: list[FxPoint]) -> None:
        self._prices: dict[InstrumentId, list[PricePoint]] = defaultdict(list)
        self._fx: dict[Currency, list[FxPoint]] = defaultdict(list)
        seen_p: set[tuple[InstrumentId, date, datetime]] = set()
        for p in prices:
            key = (p.instrument, p.valid_date, p.known_time)
            if key in seen_p:
                raise ManualDataError(
                    f"duplicate price {p.instrument} {p.valid_date} {p.known_time}"
                )
            seen_p.add(key)
            self._prices[p.instrument].append(p)
        seen_f: set[tuple[Currency, date, datetime]] = set()
        for f in fx:
            key2 = (f.currency, f.valid_date, f.known_time)
            if key2 in seen_f:
                raise ManualDataError(f"duplicate fx {f.currency} {f.valid_date} {f.known_time}")
            seen_f.add(key2)
            self._fx[f.currency].append(f)
        for plist in self._prices.values():
            plist.sort(key=lambda p: (p.valid_date, p.known_time))
        for flist in self._fx.values():
            flist.sort(key=lambda f: (f.valid_date, f.known_time))

    @classmethod
    def from_dir(cls, directory: Path) -> ManualMarketData:
        """Load prices.csv and fx.csv; either file may be absent."""
        prices = _load(directory / "prices.csv", PRICE_COLUMNS, _price)
        fx = _load(directory / "fx.csv", FX_COLUMNS, _fx)
        return cls(prices, fx)

    @staticmethod
    def _pick[T: (PricePoint, FxPoint)](items: list[T], on: date, as_of: datetime) -> T | None:
        best: T | None = None
        for item in items:  # sorted by (valid_date, known_time)
            if item.valid_date > on:
                break
            if item.known_time <= as_of:
                best = item  # later valid_date, or a later revision of the same date
        return best

    def price(self, instrument: InstrumentId, on: date, *, as_of: datetime) -> PricePoint | None:
        return self._pick(self._prices.get(instrument, []), on, as_of)

    def fx_try(self, currency: Currency, on: date, *, as_of: datetime) -> FxPoint | None:
        return self._pick(self._fx.get(currency, []), on, as_of)
