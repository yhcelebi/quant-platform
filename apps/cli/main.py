"""``qp`` CLI entry point. Commands grow phase by phase (imports, backfills, backtests...)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

import qp
from apps.cli import (
    alerts_cmd,
    data_cmd,
    ledger_cmd,
    macro_cmd,
    news_cmd,
    perf_cmd,
    portfolio_cmd,
    ref_cmd,
    research_cmd,
    risk_cmd,
)
from qp.core.errors import ConfigError
from qp.platform import init_platform
from qp.platform.health import Status, run_checks

app = typer.Typer(no_args_is_help=True, help="qp — personal quant platform.")
app.add_typer(ref_cmd.app, name="ref")
app.add_typer(ledger_cmd.app, name="ledger")
app.add_typer(portfolio_cmd.app, name="portfolio")
app.add_typer(data_cmd.app, name="data")
app.add_typer(perf_cmd.app, name="perf")
app.add_typer(risk_cmd.app, name="risk")
app.add_typer(alerts_cmd.app, name="alerts")
app.add_typer(macro_cmd.app, name="macro")
app.add_typer(news_cmd.app, name="news")
app.add_typer(research_cmd.app, name="research")
console = Console()

_STYLE = {Status.OK: "green", Status.WARN: "yellow", Status.FAIL: "red"}


@app.command()
def version() -> None:
    """Print the qp version."""
    console.print(f"qp {qp.__version__}")


@app.command()
def doctor(
    config_dir: Annotated[
        Path | None, typer.Option(help="Config directory (default: ./configs or QP_CONFIG_DIR).")
    ] = None,
    mode: Annotated[str | None, typer.Option(help="backtest | paper | live")] = None,
    alembic_ini: Annotated[Path, typer.Option(help="Path to alembic.ini.")] = Path("alembic.ini"),
) -> None:
    """Check environment, config, database, migrations and lake. Exit 1 on any failure."""
    try:
        settings = init_platform(config_dir=config_dir, mode=mode)
    except ConfigError as exc:
        console.print(f"[red]config: FAIL[/red] {exc}")
        raise typer.Exit(code=1) from None

    results = run_checks(settings, alembic_ini=alembic_ini if alembic_ini.is_file() else None)
    table = Table(title=f"qp doctor (mode={settings.mode})")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    table.add_row("config", "[green]ok[/green]", f"fingerprint {settings.fingerprint()[:12]}")
    for r in results:
        table.add_row(r.name, f"[{_STYLE[r.status]}]{r.status}[/]", r.detail)
    console.print(table)
    if any(r.status is Status.FAIL for r in results):
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
