from pathlib import Path

import pytest

from qp.platform import load_settings
from qp.platform.health import Status, check_mode, run_checks


def test_run_checks_without_database(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QP_LAKE__ROOT", str(tmp_path / "lake"))
    monkeypatch.setenv("QP_DB__HOST", "127.0.0.1")
    monkeypatch.setenv("QP_DB__PORT", "1")  # nothing listens here
    monkeypatch.setenv("QP_DB__CONNECT_TIMEOUT_S", "1")
    results = {r.name: r for r in run_checks(load_settings(config_dir=config_dir, env_file=None))}
    assert results["python"].status is Status.OK
    assert results["tzdata"].status is Status.OK
    assert results["lake"].status is Status.OK
    assert results["notifier"].status is Status.WARN
    assert results["database"].status is Status.FAIL
    assert "schemas" not in results
    assert "\n" not in results["database"].detail


def test_mode_check_flags_stray_live_ack(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(config_dir=config_dir, env_file=None)
    assert check_mode(settings).status is Status.OK
    monkeypatch.setenv("QP_LIVE_TRADING", "I_UNDERSTAND_THE_RISK")
    assert check_mode(settings).status is Status.WARN
    live = load_settings(config_dir=config_dir, mode="live", env_file=None)
    assert check_mode(live).status is Status.WARN
