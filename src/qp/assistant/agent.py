"""The read-only assistant loop (ADR 0016, 10-13).

Claude answers with the read-only tools of :mod:`qp.assistant.tools`; after the answer, every
number in it is checked against the tool results and the question (:mod:`qp.assistant.guard`).
The client is injected: production passes ``anthropic.Anthropic(...).beta.messages``, tests a
fake with the same ``create`` call.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from qp.assistant.guard import unverified
from qp.assistant.tools import Json, Tool
from qp.core import QPError
from qp.platform.settings import AssistantSettings

SYSTEM = """\
You answer questions about the owner's personal investment platform (qp) using only the \
read-only tools provided. The tools return data computed by qp; they cannot change anything.

Rules:
- Every number you write must be copied from a tool result (or the question), with its \
unit and the date it refers to. Never compute a number yourself: no sums, differences, \
ratios, averages, conversions, annualizing or rounding beyond what the tool returned. If a \
figure is not in the tool results, say it is not available and name the tool that would \
need the data.
- When a tool reports something as unavailable, missing, stale or left out, say so.
- Describe what the data and models show; do not tell the owner what to buy or sell. \
Optimization weights and scenario results are model outputs under stated assumptions.
- Be brief. Cite the tool(s) you used at the end, e.g. "(from: risk, 2026-10-24)".
- Answer in the language of the question.
"""

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class MessagesClient(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    input: Json
    ok: bool
    summary: str  # day / known_at the result describes, or the error


@dataclass
class Answer:
    question: str
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    stop_reason: str = ""
    model: str = ""
    notes: list[str] = field(default_factory=list)


def _default(x: object) -> object:
    if isinstance(x, (Decimal, date, datetime, Path)):
        return str(x)
    raise TypeError(f"not JSON serializable: {type(x).__name__}")


def _summary(result: Json) -> str:
    for key in ("day", "end", "known_at"):
        if result.get(key):
            return f"{key} {result[key]}"
    return ""


def api_key(settings: AssistantSettings) -> str | None:
    """QP_ASSISTANT__API_KEY, else ANTHROPIC_API_KEY (env/.env only, rule 9)."""
    if settings.api_key is not None and settings.api_key.get_secret_value():
        return settings.api_key.get_secret_value()
    return os.environ.get("ANTHROPIC_API_KEY") or None


class AssistantUnavailableError(QPError):
    """No API key: the assistant is off (ADR 0016, 13)."""


def anthropic_client(settings: AssistantSettings) -> MessagesClient:
    key = api_key(settings)
    if key is None:
        raise AssistantUnavailableError(
            "the assistant is off: set QP_ASSISTANT__API_KEY or ANTHROPIC_API_KEY in .env "
            "(questions and portfolio figures are then sent to Anthropic's API)"
        )
    import anthropic

    return cast(MessagesClient, anthropic.Anthropic(api_key=key).beta.messages)


class Assistant:
    def __init__(
        self,
        client: MessagesClient,
        tools: Sequence[Tool],
        settings: AssistantSettings,
        *,
        fallbacks: bool = True,
    ) -> None:
        self.client = client
        self.tools = {t.name: t for t in tools}
        self.settings = settings
        self.fallbacks = fallbacks
        self.history: list[Any] = []  # the last conversation, to continue it

    def _request(self, messages: list[Any]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "max_tokens": 16000,
            "system": SYSTEM,
            "tools": [t.schema() for t in self.tools.values()],
            "messages": messages,
            "output_config": {"effort": self.settings.effort},
        }
        if self.fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return self.client.create(**kwargs)

    def _run_tool(self, name: str, args: Json) -> tuple[str, bool, Json | None, str]:
        tool = self.tools.get(name)
        if tool is None:
            return f"unknown tool {name}", False, None, "unknown tool"
        try:
            result = tool.run(args)
        except (QPError, ValueError, KeyError, TypeError) as exc:
            return f"error: {exc}", False, None, str(exc)
        return json.dumps(result, default=_default, sort_keys=True), True, result, _summary(result)

    def ask(self, question: str, history: Sequence[Any] = ()) -> Answer:
        messages: list[Any] = [*history, {"role": "user", "content": question}]
        answer = Answer(question, "", model=self.settings.model)
        results: list[Any] = [question]
        texts: list[str] = []
        for _ in range(self.settings.max_rounds):
            response = self._request(messages)
            answer.model = str(getattr(response, "model", "") or answer.model)
            answer.stop_reason = str(response.stop_reason)
            messages.append({"role": "assistant", "content": response.content})
            texts = [b.text for b in response.content if b.type == "text"]
            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use":
                break
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                args = dict(block.input or {})
                content, ok, data, summary = self._run_tool(block.name, args)
                answer.tool_calls.append(ToolCall(block.name, args, ok, summary))
                if data is not None:
                    results.append(data)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": content,
                        "is_error": not ok,
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        else:
            answer.notes.append(f"stopped after {self.settings.max_rounds} rounds of tool calls")
        answer.text = "\n".join(texts).strip()
        if answer.stop_reason == "refusal":
            answer.notes.append("the model declined to answer")
        elif answer.stop_reason == "max_tokens":
            answer.notes.append("the answer was cut off at the token limit")
        answer.unverified = unverified(answer.text, results)
        self.history = messages
        return answer


def log_transcript(path: Path, answer: Answer) -> None:
    """Append one line per answer to the local transcript file (never committed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": datetime.now(UTC).isoformat(), **asdict(answer)}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=_default, ensure_ascii=False) + "\n")
