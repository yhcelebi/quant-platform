"""Parquet lake paths.

    <root>/raw/{source}/{dataset}/fetched_date=YYYY-MM-DD/   immutable, as fetched
    <root>/clean/{dataset}/
    <root>/derived/{dataset}/
    <root>/results/{backtests|studies}/{id}/

Only path construction and validation live here; reading/writing is qp.data.lake (phase 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from qp.core.errors import QPValidationError

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
ResultKind = Literal["backtests", "studies"]
ZONES: tuple[str, ...] = ("raw", "clean", "derived", "results")


def _name(value: str, what: str) -> str:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise QPValidationError(f"invalid lake {what}: {value!r} (use [a-z0-9_-])")
    return value


@dataclass(frozen=True, slots=True)
class LakePaths:
    root: Path

    def raw(self, source: str, dataset: str, fetched_date: date) -> Path:
        if not isinstance(fetched_date, date):
            raise TypeError("fetched_date must be a date")
        return (
            self.root
            / "raw"
            / _name(source, "source")
            / _name(dataset, "dataset")
            / f"fetched_date={fetched_date.isoformat()}"
        )

    def clean(self, dataset: str) -> Path:
        return self.root / "clean" / _name(dataset, "dataset")

    def derived(self, dataset: str) -> Path:
        return self.root / "derived" / _name(dataset, "dataset")

    def results(self, kind: ResultKind, result_id: str) -> Path:
        if kind not in ("backtests", "studies"):
            raise QPValidationError(f"invalid result kind: {kind!r}")
        return self.root / "results" / kind / _name(result_id, "result id")

    def ensure_zones(self) -> None:
        """Create the four top-level zone directories if missing."""
        for zone in ZONES:
            (self.root / zone).mkdir(parents=True, exist_ok=True)
