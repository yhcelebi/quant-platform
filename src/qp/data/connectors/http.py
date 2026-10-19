"""HTTP for connectors: one client per source, rate-limited and retried politely (rule 11).

- At most one request per ``min_interval_s``.
- Retries on timeouts / connection errors, 429 and 5xx, with exponential backoff; a numeric
  ``Retry-After`` header is honoured. Other statuses (e.g. 404) are returned to the caller.
- Only ``safe_url`` (the caller's URL with credentials removed) is ever logged or stored.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import TracebackType

import httpx

from qp.core import QPError
from qp.platform.logging import get_logger

log = get_logger("qp.data.http")
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class FetchError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    content: bytes
    safe_url: str
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        min_interval_s: float,
        timeout_s: float = 30.0,
        retries: int = 3,
        backoff_s: float = 2.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout_s,
            transport=transport,
            follow_redirects=False,
        )
        self.min_interval_s = min_interval_s
        self.retries = retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._last: float | None = None

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self.min_interval_s - (self._monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._monotonic()

    def get(
        self, url: str, *, safe_url: str | None = None, headers: Mapping[str, str] | None = None
    ) -> HttpResult:
        shown = safe_url or url
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                resp = self._client.get(url, headers=dict(headers or {}))
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise FetchError(f"GET {shown} failed: {type(exc).__name__}") from None
                log.warning("http.retry", url=shown, error=type(exc).__name__, attempt=attempt)
                self._sleep(self.backoff_s * 2**attempt)
                continue
            if resp.status_code in RETRY_STATUSES and attempt < self.retries:
                delay = self.backoff_s * 2**attempt
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    delay = max(delay, float(retry_after))
                log.warning("http.retry", url=shown, status=resp.status_code, attempt=attempt)
                self._sleep(delay)
                continue
            log.info("http.get", url=shown, status=resp.status_code, bytes=len(resp.content))
            return HttpResult(resp.status_code, resp.content, shown, dict(resp.headers))
        raise AssertionError("unreachable")  # pragma: no cover
