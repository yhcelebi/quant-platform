"""Job-run records (``ops.job_runs``): every fetch, backfill or check leaves a row with its
outcome, so ``qp data status`` and the terminal can show what ran and what failed."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from qp.platform.db import Base, Database

JOB_STATUSES = ("running", "ok", "partial", "failed")


class JobRunRow(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in JOB_STATUSES)})", name="status"),
        {"schema": "ops"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default="running")
    detail: Mapped[str] = mapped_column(Text, server_default="")


@dataclass
class JobRun:
    id: int
    job: str
    status: str = "ok"
    lines: list[str] = field(default_factory=list)

    def note(self, line: str) -> None:
        self.lines.append(line)


def _redact(text: str, secrets: Sequence[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


@contextmanager
def job_run(db: Database, job: str, *, secrets: Sequence[str] = ()) -> Iterator[JobRun]:
    """Record a run: 'running' on entry, then the status the body set ('ok' by default), or
    'failed' with the error if it raised (the error is re-raised). Secrets are scrubbed."""
    with db.session() as s:
        row = JobRunRow(job=job)
        s.add(row)
        s.flush()
        run = JobRun(row.id, job)
    try:
        yield run
    except BaseException as exc:
        run.status = "failed"
        run.note(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        with db.session() as s:
            stored = s.get(JobRunRow, run.id)
            assert stored is not None
            stored.status = run.status
            stored.detail = _redact("\n".join(run.lines), secrets)
            stored.finished_at = func.now()


def recent_runs(db: Database, limit: int = 20) -> list[JobRunRow]:
    with db.session() as s:
        rows = s.scalars(select(JobRunRow).order_by(JobRunRow.id.desc()).limit(limit)).all()
        s.expunge_all()
        return list(rows)
