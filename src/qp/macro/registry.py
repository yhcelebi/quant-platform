"""Indicator registry (ADR 0012, 1): ``configs/indicators.yaml``."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from qp.core import QPError
from qp.data.series import SeriesData, SeriesPoint
from qp.macro.transforms import Frequency, Obs, Transform, apply, observations


class RegistryError(QPError):
    pass


class Indicator(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    series: str
    frequency: Frequency
    unit: str
    category: str
    country: str
    note: str = ""


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    indicators: tuple[Indicator, ...]

    def get(self, indicator_id: str) -> Indicator:
        for i in self.indicators:
            if i.id == indicator_id:
                return i
        raise RegistryError(f"unknown indicator {indicator_id!r}")

    @property
    def categories(self) -> list[str]:
        return sorted({i.category for i in self.indicators})


def load_registry(path: Path) -> Registry:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        registry = Registry.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise RegistryError(f"{path}: {exc}") from exc
    ids = [i.id for i in registry.indicators]
    if len(ids) != len(set(ids)):
        raise RegistryError(f"{path}: duplicate indicator ids")
    return registry


def indicator_history(
    indicator: Indicator,
    series: SeriesData,
    *,
    until: date,
    as_of: datetime,
    transform: Transform = Transform.LEVEL,
) -> tuple[list[Obs], list[SeriesPoint]]:
    """The transformed observations and the raw points they came from (as known at
    ``as_of``)."""
    points = series.history(indicator.series, until, as_of=as_of)
    return apply(transform, observations(points), indicator.frequency), points
