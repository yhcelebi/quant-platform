"""Shared CLI helpers: settings bootstrap, console, number formatting."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console

from qp.core.errors import QPError
from qp.platform import Settings, init_platform

console = Console()


def settings(config_dir: Path | None = None, mode: str | None = None) -> Settings:
    try:
        return init_platform(config_dir=config_dir, mode=mode)
    except QPError as exc:
        fail(f"config: {exc}")


def fail(message: str, code: int = 1) -> NoReturn:
    console.print(f"[red]error:[/red] {message}")
    raise typer.Exit(code=code)


def money(value: Decimal | None, places: int = 2) -> str:
    """Display formatting only (rounded for reading; stored values are exact)."""
    if value is None:
        return "—"
    return f"{value:,.{places}f}"


def qty(value: Decimal | None) -> str:
    if value is None:
        return "—"
    text = f"{value.normalize():f}"
    return text
