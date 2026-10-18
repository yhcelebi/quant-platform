"""Structured JSON logging (structlog) with UTC timestamps and secret redaction (rule 10)."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable, MutableMapping
from typing import Any, TextIO

import structlog
from pydantic import SecretStr
from structlog.typing import EventDict, Processor, WrappedLogger

from qp.platform.settings import LogSettings

REDACTED = "***"
_SECRET_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|private[_-]?key|credential)",
    re.IGNORECASE,
)
_MIN_SECRET_LEN = 4


class SecretRedactor:
    """structlog processor that masks secrets by key name, by type and by known value."""

    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        self._known = sorted({s for s in known_secrets if len(s) >= _MIN_SECRET_LEN}, key=len)
        self._known.reverse()  # replace longer secrets first

    def _scrub_text(self, text: str) -> str:
        for secret in self._known:
            text = text.replace(secret, REDACTED)
        return text

    def _scrub(self, key: str | None, value: Any) -> Any:
        if isinstance(value, SecretStr):
            return REDACTED
        if key is not None and _SECRET_KEY_RE.search(key) and value not in (None, ""):
            return REDACTED
        if isinstance(value, str):
            return self._scrub_text(value)
        if isinstance(value, MutableMapping):
            return {k: self._scrub(str(k), v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return type(value)(self._scrub(None, v) for v in value)
        return value

    def __call__(self, logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        return {k: self._scrub(k if k != "event" else None, v) for k, v in event_dict.items()}


def configure_logging(
    settings: LogSettings,
    *,
    known_secrets: Iterable[str] = (),
    stream: TextIO | None = None,
) -> None:
    """Configure structlog process-wide. Call once at process start-up."""
    level = logging.getLevelNamesMapping().get(settings.level.upper())
    if level is None:
        raise ValueError(f"unknown log level: {settings.level!r}")
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.json_format
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        SecretRedactor(known_secrets),
        renderer,
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stderr),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None, **initial: Any) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name, **initial)
    return logger
