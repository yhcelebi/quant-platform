"""Dated rates (taxes, commissions) as configuration data, never constants in code.

File format (``configs/rates/*.yaml``)::

    rates:
      - key: withholding.dividend.BIST.equity   # free-form dotted key
        rate: "0.15"                           # fraction, as a string
        effective_from: 2024-12-22
        source: "Official Gazette ..., decision no. ..."   # required citation

A rate applies from ``effective_from`` until the next entry for the same key.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from qp.core import ConfigError, QPValidationError, to_decimal


@dataclass(frozen=True, slots=True)
class DatedRate:
    key: str
    rate: Decimal
    effective_from: date
    source: str


class RateTable:
    def __init__(self, entries: list[DatedRate]) -> None:
        by_key: dict[str, list[DatedRate]] = defaultdict(list)
        for e in entries:
            if not (0 <= e.rate <= 1):
                raise QPValidationError(f"rate {e.key} must be a fraction in [0, 1], got {e.rate}")
            if not e.source.strip():
                raise QPValidationError(f"rate {e.key} from {e.effective_from} has no source")
            by_key[e.key].append(e)
        self._by_key: dict[str, list[DatedRate]] = {}
        for key, items in by_key.items():
            items.sort(key=lambda r: r.effective_from)
            dates = [r.effective_from for r in items]
            if len(set(dates)) != len(dates):
                raise QPValidationError(f"duplicate effective_from for rate {key}")
            self._by_key[key] = items

    def lookup(self, key: str, on: date) -> DatedRate | None:
        """The rate in force for ``key`` on date ``on``, or None if none is configured."""
        items = self._by_key.get(key)
        if not items:
            return None
        idx = bisect.bisect_right([r.effective_from for r in items], on) - 1
        return items[idx] if idx >= 0 else None

    @classmethod
    def from_dir(cls, directory: Path) -> RateTable:
        entries: list[DatedRate] = []
        for path in sorted(directory.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ConfigError(f"{path} must be a mapping")
            for raw in data.get("rates") or []:
                try:
                    effective = raw["effective_from"]
                    if not isinstance(effective, date):
                        effective = date.fromisoformat(str(effective))
                    entries.append(
                        DatedRate(
                            key=str(raw["key"]),
                            rate=to_decimal(str(raw["rate"])),
                            effective_from=effective,
                            source=str(raw.get("source", "")),
                        )
                    )
                except (KeyError, ValueError) as exc:
                    raise ConfigError(f"invalid rate entry in {path}: {raw!r} ({exc})") from exc
        return cls(entries)
