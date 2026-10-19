"""Smoke tests for the app shells. No broker, no network, no database needed."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

from apps.cli.main import app as cli_app
from apps.terminal.app import PAGES
from apps.trader.main import EXIT_REFUSED
from apps.trader.main import main as trader_main
from apps.workers.main import build_scheduler
from apps.workers.main import main as workers_main
from qp.platform import load_settings

REPO = Path(__file__).resolve().parents[3]
TERMINAL = REPO / "apps" / "terminal" / "app.py"


def test_cli_version() -> None:
    result = CliRunner().invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert result.output.startswith("qp ")


def test_cli_doctor_fails_without_db(in_tmp_cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QP_DB__HOST", "127.0.0.1")
    monkeypatch.setenv("QP_DB__PORT", "1")
    result = CliRunner().invoke(cli_app, ["doctor"])
    assert result.exit_code == 1
    assert "database" in result.output


def test_cli_doctor_reports_config_error(in_tmp_cwd: Path) -> None:
    result = CliRunner().invoke(cli_app, ["doctor", "--mode", "yolo"])
    assert result.exit_code == 1
    assert "config: FAIL" in result.output


def test_terminal_default_page_renders(in_tmp_cwd: Path) -> None:
    at = AppTest.from_file(str(TERMINAL), default_timeout=30).run()
    assert not at.exception
    assert at.title[0].value == "Overview"


@pytest.mark.parametrize(("file", "title"), [(f, t) for f, t, _ in PAGES])
def test_terminal_every_page_renders(
    file: str, title: str, in_tmp_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QP_DB__HOST", "127.0.0.1")
    monkeypatch.setenv("QP_DB__PORT", "1")  # no database: pages must degrade gracefully
    at = AppTest.from_file(str(TERMINAL), default_timeout=30).run()
    at.switch_page(f"pages/{file}").run()
    assert not at.exception
    assert at.title[0].value == title


def test_workers_once(in_tmp_cwd: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert workers_main(["--once", "--job", "heartbeat"]) == 0  # never the network in tests
    events = [json.loads(line)["event"] for line in capsys.readouterr().err.splitlines()]
    assert "workers.heartbeat" in events
    assert "workers.tcmb_fx" not in events


def test_workers_schedule(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = build_scheduler(load_settings(config_dir=config_dir, env_file=None))
    assert [j.id for j in scheduler.get_jobs()] == ["heartbeat", "tcmb_fx", "tcmb_fx_retry"]
    tcmb = scheduler.get_job("tcmb_fx")
    assert tcmb is not None
    assert str(tcmb.trigger.timezone) == "Europe/Istanbul"
    monkeypatch.setenv("QP_SOURCES__FRED__API_KEY", "k")  # keyed sources join the schedule
    keyed = build_scheduler(load_settings(config_dir=config_dir, env_file=None))
    assert "fred" in [j.id for j in keyed.get_jobs()]
    assert "evds" not in [j.id for j in keyed.get_jobs()]  # no key and no series


def test_trader_paper_runs(in_tmp_cwd: Path) -> None:
    assert trader_main([]) == 0


@pytest.mark.parametrize("mode", ["live", "backtest"])
def test_trader_refuses_non_paper(in_tmp_cwd: Path, mode: str) -> None:
    assert trader_main(["--mode", mode]) == EXIT_REFUSED


def test_trader_refuses_live_even_with_ack(
    in_tmp_cwd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QP_LIVE_TRADING", "I_UNDERSTAND_THE_RISK")
    assert trader_main(["--mode", "live"]) == EXIT_REFUSED
