"""Decisions journal on a throwaway database: append-only, derived status, review alerts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from qp.decisions import journal
from qp.platform import Database
from qp.platform.notifier import Level
from qp.reporting.daily import send_review_alerts

pytestmark = pytest.mark.integration


class Sink:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str, *, level: Level = "info") -> None:
        self.sent.append(text)


def test_journal_flow(migrated_db: Database, tmp_path: Path) -> None:
    db = migrated_db
    tid = journal.add_thesis(
        db,
        title="TRY real rates stay positive",
        thesis="Policy rate above expected inflation through 2027",
        invalidation="Policy rate cut below 12-month expected inflation",
        first_review=date(2026, 11, 1),
        direction="macro",
    )
    with pytest.raises(Exception, match="invalidation"):
        journal.add_thesis(
            db, title="x", thesis="y", invalidation=" ", first_review=date(2026, 1, 1)
        )
    journal.add_entry(db, "MPC held rates", thesis_id=tid)
    [t] = journal.theses(db)
    assert (t.status, t.next_review) == ("open", date(2026, 11, 1))

    sink = Sink()
    assert len(send_review_alerts(db, date(2026, 11, 2), sink)) == 1
    assert send_review_alerts(db, date(2026, 11, 3), sink) == []  # once per review date
    assert "Review due (2026-11-01): #1 TRY real rates stay positive" in sink.sent[0]

    with pytest.raises(Exception, match="next review"):
        journal.review(db, tid, verdict="keep")
    journal.review(db, tid, verdict="keep", next_review=date(2027, 1, 15), note="still positive")
    assert journal.theses(db)[0].next_review == date(2027, 1, 15)
    journal.review(db, tid, verdict="invalidated", note="cut below inflation")
    assert journal.theses(db)[0].status == "invalidated"
    with pytest.raises(Exception, match="already invalidated"):
        journal.review(db, tid, verdict="close")
    assert journal.due(db, date(2030, 1, 1)) == []

    with db.engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text("UPDATE journal.theses SET title = 'edited'"))
    with db.engine.begin() as conn, pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text("DELETE FROM journal.entries"))
