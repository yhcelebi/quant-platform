"""Study runner: provenance, never overwriting, and as_of binding (ADR 0013, 7)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from qp.platform import load_settings
from qp.reporting.studies import StudyError, list_studies, read_run, run_study

REPO = Path(__file__).resolve().parents[3]

STUDY_PY = """
from datetime import date, timedelta

def run(ctx):
    ctx.note("toy study")
    draws = ctx.rng.normal(size=3).tolist()
    later = None
    try:
        ctx.series.history("X", date(2030, 1, 1), as_of=ctx.as_of + timedelta(days=1))
    except Exception as exc:
        later = type(exc).__name__
    return {"k": ctx.params["k"], "draws": draws, "later_view": later, "day": ctx.day}
"""


def make_study(root: Path, as_of: str = "2026-01-02T10:00:00+03:00") -> Path:
    folder = root / "toy"
    folder.mkdir(parents=True)
    (folder / "study.yaml").write_text(
        f"title: Toy\nhypothesis: none\nas_of: '{as_of}'\nseed: 5\nparams: {{k: 1}}\n"
    )
    (folder / "study.py").write_text(STUDY_PY)
    return root


def test_run_writes_result_and_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QP_LAKE__ROOT", str(tmp_path / "lake"))
    settings = load_settings(config_dir=REPO / "configs", env_file=None)
    studies = make_study(tmp_path / "studies")
    t0 = datetime(2026, 10, 25, 9, tzinfo=UTC)
    first = run_study(settings, "toy", studies_dir=studies, now=t0, repo=REPO)
    again = run_study(
        settings, "toy", studies_dir=studies, now=t0 + timedelta(seconds=1), repo=REPO
    )
    assert first.result["draws"] == again.result["draws"]  # seeded
    assert first.result["later_view"] == "StudyError"  # cannot look past as_of
    assert first.result["day"] == date(2026, 1, 2)  # Istanbul calendar day of as_of
    prov = json.loads((first.folder / "provenance.json").read_text())
    assert prov["seed"] == 5
    assert prov["params"] == {"k": 1}
    assert prov["notes"] == ["toy study"]
    assert len(prov["git_commit"]) == 40
    assert set(prov["lake_snapshot"]) >= {"tcmb_fx_daily", "calendar_events"}
    assert "numpy" in prov["packages"]
    over = run_study(
        settings,
        "toy",
        studies_dir=studies,
        overrides={"k": 2},
        now=t0 + timedelta(seconds=2),
        repo=REPO,
    )
    assert over.result["k"] == 2
    [info] = list_studies(settings, studies)
    assert len(info.runs) == 3  # every run kept
    assert read_run(info.runs[0]).result["k"] == 1
    with pytest.raises(FileExistsError):
        run_study(settings, "toy", studies_dir=studies, now=t0, repo=REPO)


def test_spec_needs_an_offset(tmp_path: Path) -> None:
    settings = load_settings(config_dir=REPO / "configs", env_file=None)
    studies = make_study(tmp_path / "studies", as_of="2026-01-02T10:00:00")
    with pytest.raises(StudyError, match="UTC offset"):
        run_study(settings, "toy", studies_dir=studies)


def test_repo_studies_load() -> None:
    settings = load_settings(config_dir=REPO / "configs", env_file=None)
    ids = {s.study_id for s in list_studies(settings, REPO / "research" / "studies")}
    assert "usdtry_volatility" in ids
