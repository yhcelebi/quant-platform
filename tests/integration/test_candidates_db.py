"""Corporate-action review queue against a real database."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qp.core import TRY, InstrumentId
from qp.platform import Database
from qp.reference import candidates as c
from qp.reference.domain import AssetClass, CorporateActionType, Instrument
from qp.reference.repo import ReferenceRepository

pytestmark = pytest.mark.integration

THY = InstrumentId.parse("THYAO.BIST")
T = datetime(2026, 5, 1, 9, tzinfo=UTC)


def report(ex: date, ratio: str, source: str = "test") -> c.ReportedAction:
    return c.ReportedAction(
        THY, CorporateActionType.BONUS_ISSUE, ex, source, T, ratio=Decimal(ratio)
    )


def test_queue_propose_accept_reject(migrated_db: Database) -> None:
    with migrated_db.session() as s:
        ReferenceRepository(s).add_instrument(Instrument(THY, TRY, AssetClass.EQUITY))
        other = InstrumentId.parse("XXX.BIST")
        queued = c.propose(
            s,
            [
                report(date(2026, 6, 1), "1"),
                report(date(2026, 6, 1), "1"),  # same report twice: one candidate
                report(date(2026, 7, 1), "0.5"),
                c.ReportedAction(other, CorporateActionType.SPLIT, date(2026, 6, 1), "t", T,
                                 ratio=Decimal(2)),  # untracked instrument: ignored
            ],
        )  # fmt: skip
        assert len(queued) == 2
        first, second = c.candidates(s)
        action = c.accept(s, first.id, note="checked KAP", now=T)
        assert action.id is not None
        assert action.known_time == T  # known since first reported
        recorded = ReferenceRepository(s).corporate_action(
            THY, CorporateActionType.BONUS_ISSUE, date(2026, 6, 1)
        )
        assert recorded is not None
        assert recorded.ratio == Decimal(1)
        with pytest.raises(c.CandidateError, match="already accepted"):
            c.accept(s, first.id, note="again", now=T)
        c.reject(s, second.id, note="wrong date", now=T)
        assert c.candidates(s) == []
        # a later report matching the recorded action is ignored; different terms conflict
        assert c.propose(s, [report(date(2026, 6, 1), "1", source="other")]) == []
        conflict = c.propose(s, [report(date(2026, 6, 1), "2", source="other")])
        assert "conflicts with recorded action" in conflict[0]
        (pending,) = c.candidates(s)
        with pytest.raises(c.CandidateError, match="append-only"):
            c.accept(s, pending.id, note="x", now=T)
        assert {r.status for r in c.candidates(s, None)} == {"accepted", "rejected", "pending"}
