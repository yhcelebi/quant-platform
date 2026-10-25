"""``qp scenario``, ``qp optimize`` and ``qp ask`` on the documented example ledger.

Holdings on 14 Jan 2025 (see test_cli_perf_risk.py): THYAO 300 @ 8 = 2,400 TRY (BIST),
AAPL 10 @ 220 USD x 42 = 92,400 TRY, USD cash 0, TRY cash. TRY -20% (USD +25%): AAPL
+23,100.00. BIST -25%: THYAO -600.00.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from apps.cli.common import console
from apps.cli.main import app
from qp.platform import Database

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
EX = REPO / "docs" / "examples"


def test_scenarios_optimize_and_ask_on_examples(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_CONFIG_DIR": str(REPO / "configs"), "QP_MANUAL__DIR": str(EX),
        "QP_LAKE__ROOT": str(tmp_path / "lake"),
        "QP_SCENARIOS__FILE": str(REPO / "configs" / "scenarios.yaml"),
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("QP_ASSISTANT__API_KEY", raising=False)
    monkeypatch.setattr(console, "width", 220)
    runner = CliRunner()

    def qp(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == code, result.output
        return result.output

    qp("ref", "import", str(EX / "instruments.csv"))
    qp("ledger", "account", "add", "EX", "--name", "Example")
    qp("ledger", "import", str(EX / "ledger.csv"))

    out = qp(
        "scenario", "run", "--as-of", "2025-01-14", "--name", "TRY -20%", "--name", "BIST -25%",
        "--detail",
    )  # fmt: skip
    assert "23,100.00" in out
    assert "-600.00" in out
    listed = qp("scenario", "list")
    assert "Aug 2018 TRY crisis" in listed

    short = qp("optimize", "run", "--as-of", "2025-01-14", "--method", "hrp", code=1)
    assert "unavailable" in short

    off = qp("ask", "What is my VaR?", code=1)
    assert "assistant is off" in off
