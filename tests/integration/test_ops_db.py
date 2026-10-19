"""ops.job_runs and ops.data_quality_results against a real database."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from qp.data.quality import CheckResult, QualityStatus, save_results
from qp.data.quality.models import DataQualityResultRow
from qp.platform import Database
from qp.platform.jobs import job_run, recent_runs

pytestmark = pytest.mark.integration


def test_job_run_records_outcome_and_scrubs_secrets(migrated_db: Database) -> None:
    with job_run(migrated_db, "fetch:test", secrets=["s3cret"]) as run:
        run.note("fetched 3 days")
        run.status = "partial"
    with pytest.raises(RuntimeError), job_run(migrated_db, "fetch:boom", secrets=["s3cret"]):
        raise RuntimeError("GET https://x?api_key=s3cret failed")
    boom, ok = recent_runs(migrated_db, limit=2)
    assert (ok.job, ok.status, ok.detail) == ("fetch:test", "partial", "fetched 3 days")
    assert ok.finished_at is not None
    assert ok.finished_at >= ok.started_at
    assert boom.status == "failed"
    assert "s3cret" not in boom.detail
    assert "api_key=***" in boom.detail


def test_quality_results_are_stored(migrated_db: Database) -> None:
    with job_run(migrated_db, "quality:test") as run:
        save_results(
            migrated_db,
            [CheckResult("ds", "stale", "USD", QualityStatus.WARN, "old")],
            run.id,
        )
    with migrated_db.session() as s:
        row = s.scalars(select(DataQualityResultRow)).one()
    assert (row.dataset, row.check_name, row.subject, row.status) == ("ds", "stale", "USD", "warn")
    assert row.job_run_id == run.id
