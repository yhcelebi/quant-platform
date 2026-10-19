"""The documented CLI workflow against a real database (throwaway), using docs/examples."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

from apps.cli.common import console
from apps.cli.main import app
from qp.platform import Database

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
EX = REPO / "docs" / "examples"


def test_cli_workflow(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_CONFIG_DIR": str(REPO / "configs"), "QP_MANUAL__DIR": str(EX),
        "QP_LAKE__ROOT": str(tmp_path / "lake"),  # never the real lake
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(console, "width", 200)  # tables must not wrap numbers in narrow terminals
    runner = CliRunner()

    def qp(*args: str) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == 0, result.output
        return result.output

    qp("ref", "import", str(EX / "instruments.csv"))
    qp("ledger", "account", "add", "EX", "--name", "Example")
    assert "dry run: nothing written" in qp("ledger", "import", str(EX / "ledger.csv"), "--dry-run")
    assert "imported 8 transactions" in qp("ledger", "import", str(EX / "ledger.csv"))
    again = runner.invoke(app, ["ledger", "import", str(EX / "ledger.csv")])
    assert again.exit_code == 1
    assert "already imported" in again.output

    shown = qp("portfolio", "show", "--as-of", "2025-01-14")
    assert "92,400.00" in shown
    assert "Total TRY: 103,430.50" in shown
    assert "ledger matches the statement" in qp(
        "ledger", "reconcile", str(EX / "statement.csv"), "--as-of", "2025-01-14"
    )
    mismatch = runner.invoke(
        app, ["ledger", "reconcile", str(EX / "statement.csv"), "--as-of", "2025-01-07"]
    )
    assert mismatch.exit_code == 1
    assert "Mismatches" in mismatch.output

    listing = qp("ledger", "list")
    tx_id = next(
        line.split("│")[1].strip() for line in listing.splitlines() if "AAPL.NASDAQ" in line
    )
    assert "reversal" in qp("ledger", "reverse", tx_id, "--note", "test", "--yes")
    assert "(reversed)" in qp("ledger", "list")


def test_terminal_portfolio_page_shows_ledger(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test_cli_workflow(migrated_db, monkeypatch, tmp_path)  # populate via the CLI (+ a reversal)
    at = AppTest.from_file(str(REPO / "apps" / "terminal" / "app.py"), default_timeout=30).run()
    at.switch_page("pages/portfolio.py").run()
    at.date_input[0].set_value(date(2025, 1, 14)).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    # AAPL buy reversed in the CLI flow: TRY cash 8,630.50 + THYAO 300 x 8 + USD cash 2,001 x 42
    assert metrics["Total (TRY)"] == "95,072.50"
    assert not at.error
