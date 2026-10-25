"""Readers for stored backtest results (ADR 0014, 9), with no backtest-engine imports, so
layers that must not reach NautilusTrader (assistant, reporting) can list and read runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from qp.platform.lake import LakePaths
from qp.platform.settings import Settings


def list_runs(settings: Settings) -> list[Path]:
    root = LakePaths(settings.lake.root).root / "results" / "backtests"
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.glob("*/*") if (p / "summary.json").is_file()), key=lambda p: p.name
    )


def read_summary(folder: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((folder / "summary.json").read_text())
    note = folder / "SUPERSEDED.txt"
    data["superseded"] = note.read_text().strip() if note.is_file() else None
    return data


def read_equity(folder: Path) -> list[dict[str, str]]:
    path = folder / "equity.csv"
    if not path.is_file():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))
