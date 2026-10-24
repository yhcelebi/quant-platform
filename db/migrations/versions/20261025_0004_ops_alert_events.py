"""ops alert events (phase 3).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25 12:00:00+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("key_date", sa.Date(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_error", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint(
            "level IN ('info', 'warning', 'critical')", name=op.f("ck_alert_events_level")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_events")),
        sa.UniqueConstraint("rule", "subject", "key_date", name="uq_alert_events_key"),
        schema="ops",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("alert_events", schema="ops")
