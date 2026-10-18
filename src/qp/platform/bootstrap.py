"""Process start-up: load settings and configure logging in one call."""

from __future__ import annotations

from pathlib import Path

from qp.platform.logging import configure_logging
from qp.platform.settings import Mode, Settings, load_settings


def init_platform(
    *,
    config_dir: Path | None = None,
    mode: Mode | str | None = None,
    env_file: Path | str | None = ".env",
) -> Settings:
    """Load settings and configure structured logging with secret redaction."""
    settings = load_settings(config_dir=config_dir, mode=mode, env_file=env_file)
    configure_logging(settings.log, known_secrets=settings.secret_values())
    return settings
