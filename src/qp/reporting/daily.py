"""Cross-cutting daily checks composed here because their parts are siblings that must not
import each other (decisions | alerts): thesis reviews that are due become alerts."""

from __future__ import annotations

from datetime import date

from qp.alerts.rules import AlertCandidate
from qp.alerts.store import deliver, record
from qp.decisions.journal import due
from qp.platform.db import Database
from qp.platform.notifier import Notifier


def review_alerts(db: Database, day: date) -> list[AlertCandidate]:
    return [
        AlertCandidate(
            "thesis_review",
            f"thesis #{t.id}",
            t.next_review or day,
            "info",
            f"Review due ({t.next_review}): #{t.id} {t.title}. Invalidation: {t.invalidation}",
        )
        for t in due(db, day)
    ]


def send_review_alerts(db: Database, day: date, notifier: Notifier) -> list[int]:
    """Store and send review-due alerts once per (thesis, review date)."""
    ids = record(db, review_alerts(db, day))
    deliver(db, notifier, ids)
    return ids
