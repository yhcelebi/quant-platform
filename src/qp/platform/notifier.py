"""Notifications: a Notifier protocol, a logging fallback and a Telegram Bot API client.

Errors never include the bot token: httpx error messages contain the request URL (which
embeds the token), so they are replaced with a sanitized message.
"""

from __future__ import annotations

from typing import Literal, Protocol

import httpx
from pydantic import SecretStr

from qp.core.errors import QPError
from qp.platform.logging import get_logger
from qp.platform.settings import TelegramSettings

Level = Literal["info", "warning", "critical"]
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_MAX_LEN = 4096
_PREFIX: dict[Level, str] = {"info": "", "warning": "[WARN] ", "critical": "[CRITICAL] "}


class NotifierError(QPError):
    """A notification could not be delivered."""


class Notifier(Protocol):
    def send(self, text: str, *, level: Level = "info") -> None: ...


class LogNotifier:
    """Writes notifications to the structured log. The default when Telegram is off."""

    def __init__(self) -> None:
        self._log = get_logger("qp.notifier")

    def send(self, text: str, *, level: Level = "info") -> None:
        self._log.info("notification", level_hint=level, text=text)


class TelegramNotifier:
    def __init__(
        self,
        bot_token: SecretStr,
        chat_id: str,
        *,
        client: httpx.Client | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        if not bot_token.get_secret_value() or not chat_id:
            raise ValueError("TelegramNotifier needs a bot token and a chat id")
        self._token = bot_token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=timeout_s)

    def send(self, text: str, *, level: Level = "info") -> None:
        message = (_PREFIX[level] + text)[:TELEGRAM_MAX_LEN]
        url = f"{TELEGRAM_API}/bot{self._token.get_secret_value()}/sendMessage"
        try:
            response = self._client.post(
                url,
                json={"chat_id": self._chat_id, "text": message, "disable_web_page_preview": True},
            )
        except httpx.HTTPError as exc:
            raise NotifierError(f"telegram request failed: {type(exc).__name__}") from None
        if response.status_code != httpx.codes.OK:
            raise NotifierError(f"telegram returned HTTP {response.status_code}")
        body = response.json()
        if not body.get("ok", False):
            raise NotifierError(f"telegram error: {body.get('description', 'unknown')}")


def build_notifier(settings: TelegramSettings) -> Notifier:
    if settings.enabled and settings.bot_token is not None and settings.chat_id is not None:
        return TelegramNotifier(settings.bot_token, settings.chat_id)
    return LogNotifier()
