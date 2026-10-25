"""Scenario definitions from ``configs/scenarios.yaml`` (ADR 0016, 6-7).

Definitions are assumptions the owner states (a shock size, a window); the numbers they
produce come from today's holdings and the data in the platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from qp.core import Currency, InstrumentId, QPValidationError


@dataclass(frozen=True, slots=True)
class Shock:
    """Instant moves: ``fx`` TRY-rate changes by currency; ``instrument``, ``venue`` and
    ``asset_class`` price changes in the instrument's currency (first match in that order);
    ``rate_bp`` a parallel yield move applied as -duration x dy to instruments listed in
    ``durations``."""

    name: str
    description: str = ""
    fx: dict[Currency, Decimal] = field(default_factory=dict)
    asset_class: dict[str, Decimal] = field(default_factory=dict)
    instrument: dict[InstrumentId, Decimal] = field(default_factory=dict)
    rate_bp: Decimal = Decimal(0)
    durations: dict[InstrumentId, Decimal] = field(default_factory=dict)
    venue: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Replay:
    """A past window replayed on today's holdings."""

    name: str
    start: date
    end: date
    description: str = ""


@dataclass(frozen=True, slots=True)
class Scenarios:
    shocks: list[Shock]
    replays: list[Replay]


def _dec(value: Any, where: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise QPValidationError(f"{where}: {value!r} is not a number") from None


def _pct_map(raw: Any, where: str) -> dict[str, Decimal]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise QPValidationError(f"{where} must be a mapping")
    out = {str(k): _dec(v, f"{where}.{k}") for k, v in raw.items()}
    for k, v in out.items():
        if v <= -1:
            raise QPValidationError(f"{where}.{k}: a change of {v} would make the value negative")
    return out


def parse(data: Any) -> Scenarios:
    if not isinstance(data, dict):
        raise QPValidationError("scenarios file must be a mapping with shocks and replays")
    shocks: list[Shock] = []
    names: set[str] = set()
    for raw in data.get("shocks") or []:
        name = str(raw["name"])
        w = f"shock {name}"
        shocks.append(
            Shock(
                name,
                str(raw.get("description", "")),
                {Currency(k.upper()): v for k, v in _pct_map(raw.get("fx"), f"{w}.fx").items()},
                _pct_map(raw.get("asset_class"), f"{w}.asset_class"),
                {
                    InstrumentId.parse(k): v
                    for k, v in _pct_map(raw.get("instrument"), f"{w}.instrument").items()
                },
                _dec(raw.get("rate_bp", 0), f"{w}.rate_bp"),
                {
                    InstrumentId.parse(str(k)): _dec(v, f"{w}.durations.{k}")
                    for k, v in (raw.get("durations") or {}).items()
                },
                {k.upper(): v for k, v in _pct_map(raw.get("venue"), f"{w}.venue").items()},
            )
        )
        if name in names:
            raise QPValidationError(f"duplicate scenario name {name}")
        names.add(name)
    replays: list[Replay] = []
    for raw in data.get("replays") or []:
        name = str(raw["name"])
        start, end = raw["start"], raw["end"]
        if not isinstance(start, date) or not isinstance(end, date) or end <= start:
            raise QPValidationError(f"replay {name}: start and end must be dates, start < end")
        if name in names:
            raise QPValidationError(f"duplicate scenario name {name}")
        names.add(name)
        replays.append(Replay(name, start, end, str(raw.get("description", ""))))
    return Scenarios(shocks, replays)


def load(path: Path) -> Scenarios:
    if not path.is_file():
        return Scenarios([], [])
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")))
