"""Backtest results with provenance (ADR 0014, 9): data/lake/results/backtests/<id>/<run>/.
Never overwritten; costless runs are labelled invalid (DOMAIN)."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from qp.backtest.reference import SimResult
from qp.backtest.walkforward import Metrics, WalkForwardResult
from qp.data.service import lake_snapshot
from qp.platform.lake import LakePaths
from qp.platform.provenance import git_state, package_versions
from qp.platform.settings import Settings


def backtest_id(signal: str, instrument: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", f"{signal}-{instrument}".lower())


def _default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def store(
    settings: Settings,
    *,
    kind: str,
    spec: dict[str, Any],
    metrics: Metrics | None,
    sim: SimResult | None = None,
    walk: WalkForwardResult | None = None,
    repo: Path = Path("."),
    now: datetime | None = None,
) -> Path:
    started = now or datetime.now(UTC)
    root = LakePaths(settings.lake.root).results(
        "backtests", backtest_id(spec["signal"], spec["instrument"])
    )
    out = root / started.strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    git = git_state(repo)
    valid = spec.get("costs") != "costless"
    summary = {
        "kind": kind,
        "valid": valid,
        "invalid_reason": None
        if valid
        else "no cost model (DOMAIN: backtests without costs are invalid)",
        "spec": spec,
        "metrics": asdict(metrics) if metrics else None,
        "provenance": {
            "run_at": started.isoformat(),
            "git_commit": git.commit,
            "git_dirty": git.dirty,
            "config_fingerprint": settings.fingerprint(),
            "lake_snapshot": lake_snapshot(settings),
            "packages": {
                **package_versions(),
                "nautilus_trader": package_versions(("nautilus_trader",))["nautilus_trader"],
            },
        },
    }
    if walk is not None:
        summary["walk_forward"] = {
            "windows": [asdict(w) for w in walk.windows],
            "grid": walk.grid,
            "psr": walk.psr,
            "dsr": walk.dsr,
            "pbo": walk.pbo,
            "notes": walk.notes,
        }
        with (out / "oos_returns.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["day", "return"])
            w.writerows(zip(walk.oos_days, walk.oos_returns, strict=True))
    if sim is not None:
        with (out / "equity.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["day", "equity", "position", "target"])
            w.writerows(zip(sim.days, sim.equity, sim.position, sim.targets, strict=True))
        with (out / "fills.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["day", "quantity", "price", "fee"])
            w.writerows((f.day, f.quantity, f.price, f.fee) for f in sim.fills)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=_default))
    return out


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
