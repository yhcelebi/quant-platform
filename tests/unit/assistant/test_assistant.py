"""The read-only assistant with a scripted client, and the rule 12 guard (ADR 0016, 10-13)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from qp.assistant.agent import (
    FALLBACK_BETA,
    Assistant,
    AssistantUnavailableError,
    anthropic_client,
    api_key,
    log_transcript,
)
from qp.assistant.guard import source_numbers, unverified, written_numbers
from qp.assistant.tools import Json, Tool
from qp.platform.settings import AssistantSettings

# ---------------------------------------------------------------------------- guard


def test_numbers_match_to_display_precision_sign_and_percent() -> None:
    src = [{"pnl": "-1250.37", "twr": "12.34%", "weight": 0.4567, "day": "2026-10-24"}]
    assert unverified("You lost 1,250.37 TRY", src) == []
    assert unverified("a loss of about 1,250 TRY", src) == []  # rounded to whole TRY
    assert unverified("TWR was 12.3%", src) == []
    assert unverified("weight 45.7%", src) == []  # fraction source, percent written
    assert unverified("on 24 October 2026", src) == []
    assert unverified("You lost 1,300 TRY", src) == ["1,300"]
    assert unverified("TWR was 13.3%", src) == ["13.3%"]


def test_turkish_formatting_and_scales() -> None:
    src = [{"value_try": "1234567.89", "rate": "41.2034"}]
    assert unverified("Toplam 1.234.567,89 TL", src) == []
    assert unverified("about 1.2 million TRY", src) == []
    assert unverified("yaklaşık 1,2 milyon TL", src) == []
    assert unverified("kur 41,20", src) == []
    assert unverified("about 1.5 million TRY", src) == ["1.5 million"]


def test_small_counts_and_identifiers_are_exempt() -> None:
    assert unverified("You hold 3 positions in 2 accounts; 1-day VaR", []) == []
    assert unverified("XU100 and DGS10 are tracked", []) == []
    assert unverified("You hold 37 positions", []) == ["37"]


def test_written_and_source_numbers() -> None:
    ws = written_numbers("%12,5 and 3 k")
    assert [w.text for w in ws] == ["%12,5", "3 k"]
    assert source_numbers({"a": [1, "x 2.5 y", None, True], "b": Decimal("-3")}) == {
        Decimal(1),
        Decimal("2.5"),
        Decimal(3),
    }


# ---------------------------------------------------------------------------- agent


def _text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def _use(i: str, name: str, args: Json) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=i, name=name, input=args)


class FakeClient:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # as sent
        return self.responses.pop(0)


def _msg(stop: str, *blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop, content=list(blocks), model="claude-opus-5-5")


def _tools(log: list[Json]) -> list[Tool]:
    def risk(args: Json) -> Json:
        log.append(args)
        return {"day": "2026-10-24", "var_try": "8421.55", "level": "99.00%"}

    def broken(args: Json) -> Json:
        raise ValueError("no data")

    return [
        Tool("risk", "risk", {"day": {"type": "string"}}, (), risk),
        Tool("x", "x", {}, (), broken),
    ]


def test_tool_loop_runs_tools_and_checks_numbers() -> None:
    log: list[Json] = []
    client = FakeClient(
        [
            _msg("tool_use", _text("Checking."), _use("t1", "risk", {"day": "2026-10-24"})),
            _msg(
                "end_turn",
                _text("Your 99% 1-day VaR is 8,421.55 TRY, roughly 2.1% of value. (from: risk)"),
            ),
        ]
    )
    a = Assistant(client, _tools(log), AssistantSettings())
    ans = a.ask("What is my VaR?")
    assert log == [{"day": "2026-10-24"}]
    assert [c.name for c in ans.tool_calls] == ["risk"]
    assert ans.tool_calls[0].ok
    assert ans.tool_calls[0].summary == "day 2026-10-24"
    assert ans.unverified == ["2.1%"]  # computed by the model, not by qp
    assert ans.stop_reason == "end_turn"
    first, second = client.calls
    assert first["betas"] == [FALLBACK_BETA]
    assert first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "medium"}
    assert first["model"] == "claude-opus-5-5"
    assert {t["name"] for t in first["tools"]} == {"risk", "x"}
    result = second["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t1"
    assert json.loads(result["content"])["var_try"] == "8421.55"
    assert result["is_error"] is False


def test_tool_errors_and_unknown_tools_are_reported_to_the_model() -> None:
    client = FakeClient(
        [
            _msg("tool_use", _use("a", "x", {}), _use("b", "write_journal", {})),
            _msg("end_turn", _text("Not available.")),
        ]
    )
    ans = Assistant(client, _tools([]), AssistantSettings()).ask("q")
    results = client.calls[1]["messages"][-1]["content"]
    assert [r["is_error"] for r in results] == [True, True]
    assert "no data" in results[0]["content"]
    assert "unknown tool" in results[1]["content"]
    assert [c.ok for c in ans.tool_calls] == [False, False]


def test_round_limit_refusal_and_pause() -> None:
    loop = [_msg("tool_use", _use(str(i), "risk", {})) for i in range(3)]
    ans = Assistant(FakeClient(loop), _tools([]), AssistantSettings(max_rounds=3)).ask("q")
    assert any("3 rounds" in n for n in ans.notes)
    refused = Assistant(FakeClient([_msg("refusal")]), _tools([]), AssistantSettings()).ask("q")
    assert "declined" in refused.notes[0]
    paused = FakeClient([_msg("pause_turn", _text("...")), _msg("end_turn", _text("done"))])
    assert Assistant(paused, _tools([]), AssistantSettings()).ask("q").text == "done"


def test_no_key_means_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert api_key(AssistantSettings()) is None
    with pytest.raises(AssistantUnavailableError):
        anthropic_client(AssistantSettings())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-env")
    assert api_key(AssistantSettings()) == "k-env"
    assert api_key(AssistantSettings(api_key="k-qp")) == "k-qp"


def test_transcript_lines(tmp_path: Path) -> None:
    client = FakeClient([_msg("end_turn", _text("hello"))])
    ans = Assistant(client, [], AssistantSettings()).ask("hi")
    path = tmp_path / "a" / "t.jsonl"
    log_transcript(path, ans)
    log_transcript(path, ans)
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert [r["text"] for r in rows] == ["hello", "hello"]


def test_toolbox_tools_are_read_only_by_name() -> None:
    """No tool name suggests a write; the schema list is what the model can call."""
    from qp.assistant.tools import Toolbox
    from qp.platform.settings import Settings

    names = [t.name for t in Toolbox(Settings()).tools()]
    assert names == [
        "holdings",
        "performance",
        "risk",
        "scenarios",
        "optimize",
        "macro",
        "news",
        "journal",
        "backtests",
        "paper",
    ]
    for t in Toolbox(Settings()).tools():
        s = t.schema()
        assert s["input_schema"]["additionalProperties"] is False
