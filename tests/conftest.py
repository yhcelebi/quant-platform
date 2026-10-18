"""Shared test fixtures. Tests never reach a real broker or the network (rule 9)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs"
REFERENCE_DIR = REPO_ROOT / "tests" / "reference"

settings.register_profile("default", deadline=None)
settings.register_profile(
    "ci", deadline=None, max_examples=300, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))

# The integration database connection (e.g. from CI), captured before tests strip QP_*.
_DB_KEYS = ("QP_DB__HOST", "QP_DB__PORT", "QP_DB__NAME", "QP_DB__USER", "QP_DB__PASSWORD")
_HOST_DB_ENV = {k: os.environ[k] for k in _DB_KEYS if k in os.environ}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every QP_* variable so the host or CI environment cannot leak into tests."""
    for key in list(os.environ):
        if key.startswith("QP_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def host_db_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Re-apply the host's QP_DB__* connection variables. Integration tests only."""
    for key, value in _HOST_DB_ENV.items():
        monkeypatch.setenv(key, value)
    return dict(_HOST_DB_ENV)


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR


@pytest.fixture
def in_tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run with cwd in a temp dir (no .env, lake under tmp) but the repo's configs."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QP_CONFIG_DIR", str(CONFIG_DIR))
    monkeypatch.setenv("QP_LAKE__ROOT", str(tmp_path / "lake"))
    return tmp_path
