"""``qp ask``: a question to the read-only assistant (ADR 0016, 10-13)."""

from __future__ import annotations

from typing import Annotated

import typer

from apps.cli.common import console, fail, settings
from qp.assistant.agent import Assistant, anthropic_client, log_transcript
from qp.assistant.tools import Toolbox
from qp.core.errors import QPError


def ask(question: Annotated[str, typer.Argument(help="Your question, in any language")]) -> None:
    """Ask about your portfolio, risk, scenarios, macro or news. Answers use qp data only;
    numbers not found in qp's results are flagged."""
    cfg = settings()
    try:
        client = anthropic_client(cfg.assistant)
    except QPError as exc:
        fail(str(exc))
    toolbox = Toolbox(cfg)
    try:
        answer = Assistant(client, toolbox.tools(), cfg.assistant).ask(question)
    finally:
        toolbox.close()
    console.print(answer.text or "[yellow](no answer)[/yellow]")
    if answer.unverified:
        console.print(
            "[red]not from qp data:[/red] "
            + ", ".join(answer.unverified)
            + " — treat these numbers as unreliable"
        )
    calls = ", ".join(f"{c.name}{'' if c.ok else ' (failed)'}" for c in answer.tool_calls)
    console.print(f"[dim]tools: {calls or 'none'} · model {answer.model}[/dim]")
    for n in answer.notes:
        console.print(f"[yellow]note:[/yellow] {n}")
    log_transcript(cfg.assistant.transcripts, answer)
