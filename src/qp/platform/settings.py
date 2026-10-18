"""Settings, layered as: configs/base.yaml < configs/<mode>/*.yaml < environment / .env.

Environment variables use the ``QP_`` prefix and ``__`` for nesting (``QP_DB__HOST``).
Secrets (fields typed SecretStr) may come only from the environment or ``.env`` (rule 10);
a secret found in a YAML file is a ConfigError. See ADR 0003.

Setting ``mode: live`` arms nothing by itself. A live order path also needs the process
environment variable ``QP_LIVE_TRADING=I_UNDERSTAND_THE_RISK`` and an ``approved_live``
promotion record; that full gate lives in qp.risk / qp.execution (phase 7).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from qp.core.errors import ConfigError

LIVE_TRADING_ENV_VAR = "QP_LIVE_TRADING"
LIVE_TRADING_ACK = "I_UNDERSTAND_THE_RISK"
CONFIG_DIR_ENV_VAR = "QP_CONFIG_DIR"
MODE_ENV_VAR = "QP_MODE"


class Mode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DbSettings(_Section):
    host: str = "localhost"
    port: int = Field(default=5433, ge=1, le=65535)
    name: str = "qp"
    user: str = "qp"
    password: SecretStr = SecretStr("")
    connect_timeout_s: int = Field(default=5, ge=1)


class LakeSettings(_Section):
    root: Path = Path("data/lake")


class ManualDataSettings(_Section):
    """Owner-maintained CSV files (prices.csv, fx.csv). Gitignored under data/."""

    dir: Path = Path("data/manual")


class TelegramSettings(_Section):
    bot_token: SecretStr | None = None
    chat_id: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.bot_token.get_secret_value() and self.chat_id)


class LogSettings(_Section):
    level: str = "INFO"
    json_format: bool = True


class Settings(BaseSettings):
    """Validated platform settings. Build with :func:`load_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="QP_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    mode: Mode = Mode.PAPER
    db: DbSettings = DbSettings()
    lake: LakeSettings = LakeSettings()
    manual: ManualDataSettings = ManualDataSettings()
    telegram: TelegramSettings = TelegramSettings()
    log: LogSettings = LogSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Earlier sources win. YAML data is passed as init kwargs and must lose to env.
        return env_settings, dotenv_settings, file_secret_settings, init_settings

    def fingerprint(self) -> str:
        """SHA-256 of the non-secret settings, for result provenance (config hash)."""
        payload = self.model_dump(mode="json")
        for path in _secret_paths(type(self)):  # secrets never influence the hash
            node: Any = payload
            for part in path[:-1]:
                node = node[part]
            node.pop(path[-1], None)
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def secret_values(self) -> list[str]:
        """Plain secret values, so the logging redactor can scrub them from messages."""
        values = [self.db.password.get_secret_value()]
        if self.telegram.bot_token is not None:
            values.append(self.telegram.bot_token.get_secret_value())
        return [v for v in values if v]


def _secret_paths(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths |= _secret_paths(annotation, (*prefix, name))
        elif "SecretStr" in str(annotation):
            paths.add((*prefix, name))
    return paths


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def _reject_yaml_secrets(data: Mapping[str, Any], source: Path) -> None:
    for path in _secret_paths(Settings):
        node: Any = data
        for part in path:
            node = node.get(part) if isinstance(node, Mapping) else None
        if node not in (None, ""):
            dotted = ".".join(path)
            env = "QP_" + "__".join(p.upper() for p in path)
            raise ConfigError(
                f"secret '{dotted}' found in {source}; secrets come only from env/.env ({env})"
            )


def default_config_dir() -> Path:
    return Path(os.environ.get(CONFIG_DIR_ENV_VAR, "configs"))


def load_settings(
    *,
    config_dir: Path | None = None,
    mode: Mode | str | None = None,
    env_file: Path | str | None = ".env",
) -> Settings:
    """Load settings: base.yaml, then the mode overlay, then env / .env (highest).

    The mode is chosen by, in order: the ``mode`` argument, ``QP_MODE`` in the process
    environment, ``mode`` in base.yaml, else paper.
    """
    cfg_dir = config_dir if config_dir is not None else default_config_dir()
    base_file = cfg_dir / "base.yaml"
    if not base_file.is_file():
        raise ConfigError(f"missing base config: {base_file}")
    data = _read_yaml(base_file)
    _reject_yaml_secrets(data, base_file)

    raw_mode = mode or os.environ.get(MODE_ENV_VAR) or data.get("mode") or Mode.PAPER.value
    try:
        selected = Mode(raw_mode)
    except ValueError as exc:
        raise ConfigError(f"unknown mode {raw_mode!r}; expected one of {list(Mode)}") from exc

    for overlay in sorted((cfg_dir / selected.value).glob("*.yaml")):
        layer = _read_yaml(overlay)
        _reject_yaml_secrets(layer, overlay)
        if "mode" in layer and layer["mode"] != selected.value:
            raise ConfigError(f"{overlay} sets mode={layer['mode']!r} inside {selected} overlay")
        data = _deep_merge(data, layer)
    data["mode"] = selected.value

    if (
        env_file is not None
        and Path(env_file).is_file()
        and LIVE_TRADING_ENV_VAR in dotenv_values(env_file)
    ):
        raise ConfigError(
            f"{LIVE_TRADING_ENV_VAR} found in {env_file}; it may only be set in the process "
            "environment, deliberately, for a live session"
        )

    try:
        settings = Settings(_env_file=env_file, **data)
    except ValidationError as exc:
        raise ConfigError(f"invalid settings: {exc}") from exc
    if settings.mode is not selected:
        # QP_MODE in .env (not the process env) disagreeing with the chosen mode.
        raise ConfigError(
            f"mode conflict: selected {selected} but env/.env resolved {settings.mode}; "
            f"set {MODE_ENV_VAR} in the process environment or pass mode explicitly"
        )
    return settings


def live_trading_env_acknowledged(environ: Mapping[str, str] | None = None) -> bool:
    """True only if the *process environment* carries the live-trading acknowledgement.

    Deliberately not a Settings field: it cannot come from YAML or .env. This is one of the
    three conditions for a live order path; it is never sufficient on its own.
    """
    env = os.environ if environ is None else environ
    return env.get(LIVE_TRADING_ENV_VAR) == LIVE_TRADING_ACK
