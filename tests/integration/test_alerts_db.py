"""Alerts against a throwaway database: stored once per key, sent once, job recorded.

On the example ledger (docs/examples), 13 Jan: the day's TWR return is
11630.5 / 12230.5 - 1 = -4.91% (limit -3%) and the drawdown from the 8 Jan peak is
1.16305 / 1.29955 - 1 = -10.50% (limit -10%).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apps.cli.common import console
from apps.cli.main import app
from qp.alerts.service import run_alerts
from qp.alerts.store import recent
from qp.platform import Database, load_settings
from qp.platform.jobs import recent_runs
from qp.platform.notifier import Level, NotifierError

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
EX = REPO / "docs" / "examples"


class FakeNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def send(self, text: str, *, level: Level = "info") -> None:
        if self.fail:
            raise NotifierError("telegram returned HTTP 500")
        self.sent.append((level, text))


def test_alerts_are_sent_once(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_CONFIG_DIR": str(REPO / "configs"), "QP_MANUAL__DIR": str(EX),
        "QP_LAKE__ROOT": str(tmp_path / "lake"),
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(console, "width", 250)
    runner = CliRunner()
    for args in (
        ["ref", "import", str(EX / "instruments.csv")],
        ["ledger", "account", "add", "EX", "--name", "Example"],
        ["ledger", "import", str(EX / "ledger.csv")],
    ):
        assert runner.invoke(app, args).exit_code == 0
    settings = load_settings(config_dir=REPO / "configs", env_file=None)

    failing = FakeNotifier(fail=True)
    first = run_alerts(settings, migrated_db, day=date(2025, 1, 13), notifier=failing)
    assert {c.rule for c in first.candidates} == {"daily_loss", "drawdown"}
    assert len(first.new_ids) == 2
    assert first.errors  # delivery failure is recorded, not hidden
    assert recent_runs(migrated_db, 1)[0].status == "partial"

    ok = FakeNotifier()
    again = run_alerts(settings, migrated_db, day=date(2025, 1, 13), notifier=ok)
    assert again.new_ids == []  # same keys: not stored or sent twice
    assert ok.sent == []
    events = recent(migrated_db)
    assert {e.rule for e in events} == {"daily_loss", "drawdown"}
    assert all(e.delivery_error for e in events)

    later = run_alerts(settings, migrated_db, day=date(2025, 1, 31), notifier=ok)
    assert {c.rule for c in later.candidates} == {"stale_price"}  # recovered to -1.18% on 14 Jan
    assert [lvl for lvl, _ in ok.sent] == ["warning", "warning"]  # two stale prices, new
    assert any("THYAO.BIST price is from 2025-01-14" in t for _, t in ok.sent)

    listing = runner.invoke(app, ["alerts", "list"])
    assert listing.exit_code == 0
    assert "stale_price" in listing.output
    dry = runner.invoke(app, ["alerts", "run", "--as-of", "2025-01-13", "--dry-run"])
    assert dry.exit_code == 0
    assert "dry run: nothing stored or sent" in dry.output
