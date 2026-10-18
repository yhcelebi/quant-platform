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

# Keys that describe the CI/integration database; kept so integration tests can connect.
_DB_KEYS = {"QP_DB__HOST", "QP_DB__PORT", "QP_DB__NAME", "QP_DB__USER", "QP_DB__PASSWORD"}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip QP_* variables (except DB connection keys) so the host env cannot leak in."""
    for key in list(os.environ):
        if key.startswith("QP_") and key not in _DB_KEYS:
            monkeypatch.delenv(key, raising=False)


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
