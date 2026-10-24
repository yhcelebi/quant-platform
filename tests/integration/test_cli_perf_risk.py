"""``qp perf`` and ``qp risk`` on the documented example ledger (throwaway database).

Hand calculation (docs/examples, scope EX), values at each close:
  2 Jan deposit 10000 -> V 10000 | 3 Jan buy 100 @ 10 + 1 -> 9999 | 6 Jan buy 100 @ 20 + 2 ->
  10997 | 7 Jan sell 50 @ 30 - 1.5 -> 12995.5 | 8 Jan bonus 1:1, price 15 -> 12995.5 |
  9 Jan price 14 -> 12695.5 | 10 Jan dividend net 135, price 12 -> 12230.5 |
  13 Jan deposit 2001 USD at 41 (F = 82041), price 10 -> 93671.5 |
  14 Jan buy 10 AAPL @ 200 + 1 USD, AAPL 220 x 42, THYAO 8 -> 103430.5.
TWR = (12230.5 / 10000) x (93671.5 - 82041) / 12230.5 x 103430.5 / 93671.5 - 1 = 28.42%.
Real (TUFE_EXAMPLE Dec 100, Jan 105): 1.2842203 / 1.05 ** (12 / 31) - 1 = 26.02%.
Attribution 2 -> 14 Jan: P&L 103430.5 - 10000 - 82041 = 11389.5 = THYAO 1030.5 (2400 - 3003
+ 1498.5 + 135) + AAPL 12360 (price 8358, FX 4002: reference case d) + cash USD -2001 (USD
valued 41 on deposit, spent at the trade rate 40) + cash TRY 0.
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


def test_perf_and_risk_on_examples(
    migrated_db: Database, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = migrated_db.engine.url
    for key, value in {
        "QP_DB__HOST": url.host, "QP_DB__PORT": str(url.port), "QP_DB__NAME": url.database,
        "QP_DB__USER": url.username, "QP_DB__PASSWORD": url.password,
        "QP_CONFIG_DIR": str(REPO / "configs"), "QP_MANUAL__DIR": str(EX),
        "QP_LAKE__ROOT": str(tmp_path / "lake"),
        "QP_PERFORMANCE__INFLATION_SERIES": "TUFE_EXAMPLE",
    }.items():  # fmt: skip
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(console, "width", 220)
    runner = CliRunner()

    def qp(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == code, result.output
        return result.output

    qp("ref", "import", str(EX / "instruments.csv"))
    qp("ledger", "account", "add", "EX", "--name", "Example")
    qp("ledger", "import", str(EX / "ledger.csv"))

    shown = qp("perf", "show", "--end", "2025-01-14")
    assert "Performance 2025-01-02 → 2025-01-14 (TRY)" in shown
    assert "28.42%" in shown
    assert "103,430.50" in shown
    assert "USD/TRY" in shown  # benchmark row (no rate on 2 Jan: unavailable, with reason)
    real = qp("perf", "show", "--end", "2025-01-14", "--view", "real")
    assert "26.02%" in real
    usd = qp("perf", "show", "--end", "2025-01-14", "--view", "usd", code=1)
    assert "no USD/TRY rate" in usd  # example FX starts 13 Jan: never guessed

    attribution = qp("perf", "attribution", "--start", "2025-01-02", "--end", "2025-01-14")
    for figure in ("1,030.50", "12,360.00", "8,358.00", "4,002.00", "-2,001.00", "11,389.50"):
        assert figure in attribution
    assert "residual" not in attribution

    risk = qp("risk", "show", "--as-of", "2025-01-14")
    assert "unavailable" in risk  # 8 daily returns: VaR needs 60
    assert "AAPL.NASDAQ" in risk
    assert "Concentration: HHI" in risk
