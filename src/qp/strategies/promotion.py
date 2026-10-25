"""Strategy deployments and the promotion lifecycle (ADR 0015, 4; DOMAIN):
``research -> backtested -> paper -> approved_live -> retired`` (retired from any status).

Evidence is required: ``backtested`` needs a stored, valid (costed) walk-forward result of
the same signal. ``approved_live`` needs ``owner_confirmed``, which only an interactive owner
action provides; the agent never sets it (AGENTS.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from qp.core import QPError
from qp.platform.db import Database
from qp.risk.gate import status as current_status
from qp.risk.models import PromotionRow, StrategyRow
from qp.strategies.signals import get

NEXT = {
    "research": {"backtested", "retired"},
    "backtested": {"paper", "retired"},
    "paper": {"approved_live", "retired"},
    "approved_live": {"retired"},
    "retired": set(),
}


class PromotionError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class Deployment:
    id: int
    name: str
    signal: str
    params: dict[str, Any]
    instrument: str | None
    fx: str | None
    costs: str
    capital: Decimal
    status: str | None


def deploy(
    db: Database,
    name: str,
    *,
    signal: str,
    params: dict[str, Any],
    costs: str,
    capital: Decimal,
    instrument: str | None = None,
    fx: str | None = None,
) -> int:
    get(signal)  # unknown signals fail here
    if (instrument is None) == (fx is None):
        raise PromotionError("give exactly one of instrument or fx")
    if costs == "costless":
        raise PromotionError("a deployed strategy needs a real cost profile")
    with db.session() as s:
        row = StrategyRow(
            name=name,
            signal=signal,
            params=json.dumps(params, sort_keys=True),
            instrument=instrument,
            fx=fx,
            costs=costs,
            capital=capital,
        )
        s.add(row)
        s.flush()
        s.add(PromotionRow(strategy_id=row.id, status="research", note="deployed"))
        return row.id


def deployments(db: Database) -> list[Deployment]:
    with db.session() as s:
        rows = s.scalars(select(StrategyRow).order_by(StrategyRow.id)).all()
        names = [
            (r.id, r.name, r.signal, r.params, r.instrument, r.fx, r.costs, r.capital) for r in rows
        ]
    return [
        Deployment(i, n, sig, json.loads(p), inst, fx, c, cap, current_status(db, n))
        for i, n, sig, p, inst, fx, c, cap in names
    ]


def _check_walk_forward(evidence: str, signal: str) -> None:
    folder = Path(evidence)
    summary = folder / "summary.json"
    if not summary.is_file():
        raise PromotionError(f"evidence {evidence}: no summary.json (a stored walk-forward run)")
    data = json.loads(summary.read_text())
    if data.get("kind") != "walk_forward":
        raise PromotionError("evidence must be a walk-forward result")
    if not data.get("valid"):
        raise PromotionError("evidence is an invalid (costless) result")
    if data["spec"]["signal"] != signal:
        raise PromotionError(f"evidence is for signal {data['spec']['signal']}, not {signal}")
    if (folder / "SUPERSEDED.txt").is_file():
        raise PromotionError("evidence result is marked superseded")


def promote(
    db: Database,
    name: str,
    to_status: str,
    *,
    evidence: str = "",
    note: str = "",
    owner_confirmed: bool = False,
) -> None:
    with db.session() as s:
        row = s.scalar(select(StrategyRow).where(StrategyRow.name == name))
        if row is None:
            raise PromotionError(f"no strategy {name}")
        signal, sid = row.signal, row.id
    now = current_status(db, name) or "research"
    if to_status not in NEXT.get(now, set()):
        raise PromotionError(f"{name}: {now} cannot move to {to_status}")
    if to_status == "backtested":
        _check_walk_forward(evidence, signal)
    if to_status == "approved_live" and not owner_confirmed:
        raise PromotionError(
            "approved_live is an owner action: it needs the owner's interactive confirmation"
        )
    with db.session() as s:
        s.add(
            PromotionRow(
                strategy_id=sid,
                status=to_status,
                evidence=evidence,
                note=note,
                owner_confirmed=owner_confirmed,
            )
        )
