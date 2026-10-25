"""Study registry and runner (ADR 0013, 7).

A study is a folder ``research/studies/<id>/`` with ``study.yaml`` (title, hypothesis,
``as_of``, parameters, seed) and ``study.py`` defining ``run(ctx) -> dict``. Running it
writes ``data/lake/results/studies/<id>/<UTC timestamp>/{result.json, provenance.json}``.
Nothing is overwritten and every run is kept, so the number of trials behind a result
(for the deflated Sharpe ratio) can always be counted.

``ctx`` gives the study only point-in-time accessors bound to ``as_of``: market data
(prices, FX), series (macro) and the clean lake; a study that wants later data must change
its ``as_of`` and say so in its spec.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from qp.core import QPError, ensure_utc, istanbul_date
from qp.data.lake import CleanStore, Dataset, Row
from qp.data.market import PreloadedMarketData
from qp.data.series import SeriesData, SeriesPoint
from qp.data.service import lake_snapshot, market_data
from qp.performance.service import series_data
from qp.platform.lake import LakePaths
from qp.platform.provenance import git_state, package_versions
from qp.platform.settings import Settings

STUDIES_DIR = Path("research/studies")


class StudyError(QPError):
    pass


class StudySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    hypothesis: str
    as_of: datetime
    seed: int = 0
    params: dict[str, Any] = {}
    tags: tuple[str, ...] = ()


class BoundSeries:
    """Series access that refuses any ``as_of`` later than the study's."""

    def __init__(self, inner: SeriesData, as_of: datetime) -> None:
        self._inner = inner
        self.as_of = as_of

    def history(
        self, series: str, until: date, *, as_of: datetime | None = None
    ) -> list[SeriesPoint]:
        when = ensure_utc(as_of) if as_of is not None else self.as_of
        if when > self.as_of:
            raise StudyError(f"study is bound to as_of {self.as_of}; asked for {when}")
        return self._inner.history(series, until, as_of=when)


@dataclass
class StudyContext:
    """What a study may use. Every accessor is bound to ``as_of``: market data is preloaded
    for it, series refuse later views, and lake queries always pass it."""

    study_id: str
    as_of: datetime
    params: dict[str, Any]
    seed: int
    rng: np.random.Generator
    market: PreloadedMarketData
    series: BoundSeries
    _clean: CleanStore
    notes: list[str] = field(default_factory=list)

    @property
    def day(self) -> date:
        """The study's last calendar day (Europe/Istanbul) for valid-date queries."""
        return istanbul_date(self.as_of)

    def lake(self, ds: Dataset, **where: Any) -> list[Row]:
        """Clean lake rows of ``ds`` as known at ``as_of`` (valid_to defaults to ``day``)."""
        valid_from = where.pop("valid_from", None)
        valid_to = where.pop("valid_to", self.day)
        return self._clean.query(
            ds, as_of=self.as_of, where=where or None, valid_from=valid_from, valid_to=valid_to
        )

    def note(self, text: str) -> None:
        self.notes.append(text)


@dataclass(frozen=True, slots=True)
class StudyInfo:
    study_id: str
    spec: StudySpec
    folder: Path
    runs: list[Path]


def load_spec(folder: Path) -> StudySpec:
    try:
        data = yaml.safe_load((folder / "study.yaml").read_text(encoding="utf-8"))
        spec = StudySpec.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise StudyError(f"{folder}/study.yaml: {exc}") from exc
    if spec.as_of.tzinfo is None:
        raise StudyError(f"{folder}/study.yaml: as_of needs a UTC offset")
    return spec


def results_root(settings: Settings, study_id: str) -> Path:
    return LakePaths(settings.lake.root).results("studies", study_id)


def list_studies(settings: Settings, studies_dir: Path = STUDIES_DIR) -> list[StudyInfo]:
    out: list[StudyInfo] = []
    for folder in sorted(p for p in studies_dir.glob("*") if (p / "study.yaml").is_file()):
        root = results_root(settings, folder.name)
        runs = (
            sorted(p for p in root.glob("*") if (p / "result.json").is_file())
            if root.is_dir()
            else []
        )
        out.append(StudyInfo(folder.name, load_spec(folder), folder, runs))
    return out


def _load_run(folder: Path) -> Callable[[StudyContext], dict[str, Any]]:
    path = folder / "study.py"
    spec = importlib.util.spec_from_file_location(f"qp_study_{folder.name}", path)
    if spec is None or spec.loader is None:
        raise StudyError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "run", None)
    if not callable(fn):
        raise StudyError(f"{path} must define run(ctx) -> dict")
    return fn  # type: ignore[no-any-return]


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class StudyRun:
    study_id: str
    folder: Path
    result: dict[str, Any]
    provenance: dict[str, Any]


def run_study(
    settings: Settings,
    study_id: str,
    *,
    studies_dir: Path = STUDIES_DIR,
    overrides: dict[str, Any] | None = None,
    now: datetime | None = None,
    repo: Path = Path("."),
) -> StudyRun:
    """Run a study and store its result with provenance under a new folder."""
    folder = studies_dir / study_id
    spec = load_spec(folder)
    params = {**spec.params, **(overrides or {})}
    as_of = ensure_utc(spec.as_of)
    ctx = StudyContext(
        study_id,
        as_of,
        params,
        spec.seed,
        np.random.default_rng(spec.seed),
        PreloadedMarketData(market_data(settings), as_of=as_of, until=istanbul_date(as_of)),
        BoundSeries(series_data(settings), as_of),
        CleanStore(LakePaths(settings.lake.root)),
    )
    result = _load_run(folder)(ctx)
    if not isinstance(result, dict):
        raise StudyError(f"{study_id}: run() must return a dict")
    started = ensure_utc(now or datetime.now(UTC))
    git = git_state(repo)
    provenance = {
        "study": study_id,
        "title": spec.title,
        "hypothesis": spec.hypothesis,
        "as_of": as_of.isoformat(),
        "run_at": started.isoformat(),
        "params": params,
        "overrides": overrides or {},
        "seed": spec.seed,
        "git_commit": git.commit,
        "git_dirty": git.dirty,
        "config_fingerprint": settings.fingerprint(),
        "lake_snapshot": lake_snapshot(settings),
        "packages": package_versions(),
        "notes": ctx.notes,
    }
    out = results_root(settings, study_id) / started.strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    (out / "result.json").write_text(json.dumps(result, indent=2, default=_json_default))
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2, default=_json_default))
    return StudyRun(study_id, out, result, provenance)


def read_run(folder: Path) -> StudyRun:
    result = json.loads((folder / "result.json").read_text())
    provenance = json.loads((folder / "provenance.json").read_text())
    return StudyRun(provenance.get("study", folder.parent.name), folder, result, provenance)
