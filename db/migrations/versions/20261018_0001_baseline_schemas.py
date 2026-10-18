"""Baseline: create the operational schemas.

Revision ID: 0001
Revises:
Create Date: 2026-10-18 00:00:00+00:00

Tables arrive in later phases (ref and ledger in phase 1). Downgrade drops the schemas with
RESTRICT, so it fails instead of destroying data if any schema is not empty.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMAS = ("ref", "ledger", "trading", "news", "journal", "ops")

COMMENTS = {
    "ref": "Instrument master, aliases, venues, calendars, corporate actions",
    "ledger": "Append-only ledger: accounts, sleeves, transactions, legs, import batches",
    "trading": "Strategies, promotions, orders, fills, positions, reconciliation, state",
    "news": "News items, entities, tags, sources (metadata and own summaries only)",
    "journal": "Theses, entries, reviews, watchlists",
    "ops": "Job runs, data quality results, alerts, alert events",
}


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        op.execute(f"COMMENT ON SCHEMA {schema} IS '{COMMENTS[schema]}'")


def downgrade() -> None:
    for schema in reversed(SCHEMAS):
        op.execute(f"DROP SCHEMA IF EXISTS {schema} RESTRICT")
