"""``qp research``: study registry and runs with provenance (ADR 0013, 7)."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.table import Table

from apps.cli.common import console, fail, settings
from qp.core.errors import QPError
from qp.reporting.studies import list_studies, read_run, run_study

app = typer.Typer(no_args_is_help=True, help="Research: studies, runs and provenance.")


@app.command("list")
def list_cmd() -> None:
    """Studies in research/studies/ and how often each has run."""
    cfg = settings()
    try:
        studies = list_studies(cfg)
    except QPError as exc:
        fail(str(exc))
    t = Table("id", "title", "as_of", "runs", "last run")
    for s in studies:
        t.add_row(
            s.study_id,
            s.spec.title,
            s.spec.as_of.isoformat(),
            str(len(s.runs)),
            s.runs[-1].name if s.runs else "—",
        )
    console.print(t)


@app.command("run")
def run_cmd(
    study: Annotated[str, typer.Argument(help="Study id (folder in research/studies/)")],
    param: Annotated[list[str] | None, typer.Option(help="Override a parameter: key=JSON")] = None,
) -> None:
    """Run a study; the result and its provenance are stored, never overwritten."""
    overrides = {}
    for item in param or []:
        key, _, raw = item.partition("=")
        try:
            overrides[key] = json.loads(raw)
        except json.JSONDecodeError:
            overrides[key] = raw
    cfg = settings()
    try:
        run = run_study(cfg, study, overrides=overrides)
    except QPError as exc:
        fail(str(exc))
    console.print_json(json.dumps(run.result, default=str))
    p = run.provenance
    dirty = (
        " [yellow](uncommitted changes: not reproducible from the commit)[/yellow]"
        if p["git_dirty"]
        else ""
    )
    console.print(f"stored {run.folder}\ncommit {p['git_commit']}{dirty}")


@app.command("show")
def show_cmd(study: Annotated[str, typer.Argument(help="Study id")]) -> None:
    """The latest stored run of a study with its provenance."""
    cfg = settings()
    try:
        info = next((s for s in list_studies(cfg) if s.study_id == study), None)
    except QPError as exc:
        fail(str(exc))
    if info is None or not info.runs:
        fail(f"no runs of {study}")
    run = read_run(info.runs[-1])
    console.print(f"[bold]{info.spec.title}[/bold]\n{info.spec.hypothesis}")
    console.print_json(json.dumps(run.result))
    console.print_json(
        json.dumps(
            {
                k: run.provenance[k]
                for k in ("as_of", "run_at", "git_commit", "git_dirty", "seed", "params")
            }
        )
    )
    console.print(f"{len(info.runs)} runs in total")
