"""Corporate-action review queue (``ref.corporate_action_candidates``).

A data source that reports a split, bonus issue or dividend we have not recorded proposes a
candidate. Nothing uses candidates: prices are adjusted and the ledger is validated only with
accepted ``ref.corporate_actions``. The owner accepts (which inserts the action, append-only)
or rejects each one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from qp.core import Currency, InstrumentId, QPError, ensure_utc
from qp.reference.domain import CorporateAction, CorporateActionType
from qp.reference.models import CorporateActionCandidateRow
from qp.reference.repo import ReferenceRepository


class CandidateError(QPError):
    pass


@dataclass(frozen=True, slots=True)
class ReportedAction:
    instrument: InstrumentId
    action_type: CorporateActionType
    ex_date: date
    source: str
    reported_at: datetime
    ratio: Decimal | None = None
    amount_per_share: Decimal | None = None
    currency: Currency | None = None

    def as_action(self, note: str = "") -> CorporateAction:
        return CorporateAction(
            self.instrument,
            self.action_type,
            self.ex_date,
            self.source,
            self.reported_at,
            ratio=self.ratio,
            amount_per_share=self.amount_per_share,
            currency=self.currency,
            note=note,
        )


def propose(session: Session, reported: Sequence[ReportedAction]) -> list[str]:
    """Queue what is not already recorded; returns one line per queued or conflicting item.

    A report matching a recorded action (same instrument, type, ex-date and terms) is
    ignored. Different terms are queued too, with the conflict in the note.
    """
    repo = ReferenceRepository(session)
    lines: list[str] = []
    for r in reported:
        if repo.instrument(r.instrument) is None:
            continue  # not an instrument we track
        r.as_action()  # validates the terms for the type
        recorded = repo.corporate_action(r.instrument, r.action_type, r.ex_date)
        note = ""
        if recorded is not None:
            if recorded.same_terms(r.as_action()):
                continue
            note = f"conflicts with recorded action {recorded.id}"
        exists = session.scalars(
            select(CorporateActionCandidateRow).where(
                CorporateActionCandidateRow.source == r.source,
                CorporateActionCandidateRow.instrument_id == str(r.instrument),
                CorporateActionCandidateRow.action_type == r.action_type.value,
                CorporateActionCandidateRow.ex_date == r.ex_date,
            )
        ).one_or_none()
        if exists is not None:
            continue
        session.add(
            CorporateActionCandidateRow(
                instrument_id=str(r.instrument),
                action_type=r.action_type.value,
                ex_date=r.ex_date,
                ratio=r.ratio,
                amount_per_share=r.amount_per_share,
                currency=r.currency.code if r.currency else None,
                source=r.source,
                reported_at=ensure_utc(r.reported_at),
                note=note,
            )
        )
        lines.append(f"{r.instrument} {r.action_type} ex {r.ex_date} ({r.source}) {note}".strip())
    session.flush()
    return lines


def candidates(
    session: Session, status: str | None = "pending"
) -> list[CorporateActionCandidateRow]:
    stmt = select(CorporateActionCandidateRow).order_by(
        CorporateActionCandidateRow.ex_date, CorporateActionCandidateRow.id
    )
    if status is not None:
        stmt = stmt.where(CorporateActionCandidateRow.status == status)
    return list(session.scalars(stmt))


def _pending(session: Session, candidate_id: int) -> CorporateActionCandidateRow:
    row = session.get(CorporateActionCandidateRow, candidate_id)
    if row is None:
        raise CandidateError(f"no candidate {candidate_id}")
    if row.status != "pending":
        raise CandidateError(f"candidate {candidate_id} is already {row.status}")
    return row


def accept(session: Session, candidate_id: int, *, note: str, now: datetime) -> CorporateAction:
    """Record the candidate as a corporate action (known since it was first reported)."""
    row = _pending(session, candidate_id)
    if row.note.startswith("conflicts with recorded action"):
        raise CandidateError(
            f"candidate {candidate_id} {row.note}; recorded actions are append-only, so a "
            "correction needs an owner decision, not an accept"
        )
    reported = ReportedAction(
        InstrumentId.parse(row.instrument_id),
        CorporateActionType(row.action_type),
        row.ex_date,
        row.source,
        row.reported_at,
        ratio=row.ratio,
        amount_per_share=row.amount_per_share,
        currency=Currency(row.currency) if row.currency else None,
    )
    action = ReferenceRepository(session).add_corporate_action(reported.as_action(note))
    row.status = "accepted"
    row.action_id = action.id
    row.decided_at = ensure_utc(now)
    row.note = note
    session.flush()
    return action


def reject(session: Session, candidate_id: int, *, note: str, now: datetime) -> None:
    row = _pending(session, candidate_id)
    row.status = "rejected"
    row.decided_at = ensure_utc(now)
    row.note = note
    session.flush()
