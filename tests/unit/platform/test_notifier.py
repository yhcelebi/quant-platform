import json

import httpx
import pytest
from pydantic import SecretStr

from qp.platform import LogNotifier, NotifierError, TelegramNotifier, build_notifier
from qp.platform.settings import TelegramSettings

TOKEN = "123456:SECRET-token-value"


def notifier(handler: httpx.MockTransport) -> TelegramNotifier:
    return TelegramNotifier(SecretStr(TOKEN), "42", client=httpx.Client(transport=handler))


def test_send_posts_expected_payload() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    notifier(httpx.MockTransport(handle)).send("x" * 5000, level="critical")
    (req,) = seen
    assert req.url.path == f"/bot{TOKEN}/sendMessage"
    body = json.loads(req.content)
    assert body["chat_id"] == "42"
    assert body["text"].startswith("[CRITICAL] ")
    assert len(body["text"]) == 4096


@pytest.mark.parametrize(
    "response",
    [httpx.Response(401, json={"ok": False}), httpx.Response(200, json={"ok": False})],
)
def test_errors_raise_without_leaking_token(response: httpx.Response) -> None:
    n = notifier(httpx.MockTransport(lambda _: response))
    with pytest.raises(NotifierError) as exc:
        n.send("hi")
    assert TOKEN not in str(exc.value)


def test_transport_error_does_not_leak_token() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(NotifierError) as exc:
        notifier(httpx.MockTransport(boom)).send("hi")
    assert TOKEN not in str(exc.value)
    assert exc.value.__cause__ is None


def test_build_notifier_falls_back_to_log() -> None:
    assert isinstance(build_notifier(TelegramSettings()), LogNotifier)
    enabled = TelegramSettings(bot_token=SecretStr(TOKEN), chat_id="42")
    assert isinstance(build_notifier(enabled), TelegramNotifier)


def test_telegram_requires_credentials() -> None:
    with pytest.raises(ValueError, match="bot token"):
        TelegramNotifier(SecretStr(""), "42")
