"""Store alert events once per key and deliver new ones through a notifier."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from qp.alerts.models import AlertEventRow
from qp.alerts.rules import AlertCandidate
from qp.platform.db import Database
from qp.platform.notifier import Level, Notifier, NotifierError

_LEVELS: dict[str, Level] = {"info": "info", "warning": "warning", "critical": "critical"}


def record(db: Database, candidates: Sequence[AlertCandidate]) -> list[int]:
    """Insert candidates whose key is new; return the new event ids (in order)."""
    ids: list[int] = []
    with db.session() as s:
        for c in candidates:
            stmt = (
                insert(AlertEventRow)
                .values(
                    rule=c.rule,
                    subject=c.subject,
                    key_date=c.key_date,
                    level=c.level,
                    message=c.message,
                )
                .on_conflict_do_nothing(constraint="uq_alert_events_key")
                .returning(AlertEventRow.id)
            )
            new_id = s.execute(stmt).scalar_one_or_none()
            if new_id is not None:
                ids.append(new_id)
    return ids


def deliver(db: Database, notifier: Notifier, ids: Sequence[int]) -> list[str]:
    """Send the events (oldest first); record sent_at or the delivery error. Returns errors."""
    errors: list[str] = []
    with db.session() as s:
        rows = s.scalars(select(AlertEventRow).where(AlertEventRow.id.in_(ids))).all()
        for row in sorted(rows, key=lambda r: r.id):
            try:
                notifier.send(row.message, level=_LEVELS.get(row.level, "warning"))
            except NotifierError as exc:
                row.delivery_error = str(exc)
                errors.append(f"{row.rule} {row.subject}: {exc}")
            else:
                row.sent_at = func.now()
    return errors


def recent(db: Database, limit: int = 50) -> list[AlertEventRow]:
    with db.session() as s:
        rows = s.scalars(select(AlertEventRow).order_by(AlertEventRow.id.desc()).limit(limit)).all()
        s.expunge_all()
        return list(rows)
