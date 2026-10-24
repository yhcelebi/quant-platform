"""journal theses, reviews, entries, watchlist events; append-only (phase 5, ADR 0013).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25 18:00:00+00:00

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("journal.theses", "journal.reviews", "journal.entries", "journal.watchlists")


def _created() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "theses",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("thesis", sa.Text(), nullable=False),
        sa.Column("invalidation", sa.Text(), nullable=False),
        sa.Column("first_review", sa.Date(), nullable=False),
        _created(),
        sa.CheckConstraint(
            "direction IN ('long', 'short', 'neutral', 'macro')", name=op.f("ck_theses_direction")
        ),
        sa.CheckConstraint("length(invalidation) > 0", name=op.f("ck_theses_has_invalidation")),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["ref.instruments.id"],
            name=op.f("fk_theses_instrument_id_instruments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_theses")),
        schema="journal",
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("thesis_id", sa.BigInteger(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("next_review", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        _created(),
        sa.CheckConstraint(
            "verdict IN ('keep', 'close', 'invalidated')", name=op.f("ck_reviews_verdict")
        ),
        sa.CheckConstraint(
            "(verdict = 'keep') = (next_review IS NOT NULL)", name=op.f("ck_reviews_keep_has_next")
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id"], ["journal.theses.id"], name=op.f("fk_reviews_thesis_id_theses")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        schema="journal",
    )
    op.create_table(
        "entries",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("thesis_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _created(),
        sa.CheckConstraint("kind IN ('note', 'decision')", name=op.f("ck_entries_kind")),
        sa.ForeignKeyConstraint(
            ["thesis_id"], ["journal.theses.id"], name=op.f("fk_entries_thesis_id_theses")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_entries")),
        schema="journal",
    )
    op.create_table(
        "watchlists",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("instrument_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        _created(),
        sa.CheckConstraint("action IN ('add', 'remove')", name=op.f("ck_watchlists_action")),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["ref.instruments.id"],
            name=op.f("fk_watchlists_instrument_id_instruments"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watchlists")),
        schema="journal",
    )
    op.execute(
        """
        CREATE FUNCTION journal.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'qp: %.% is append-only (% rejected); add a new review or entry',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
        END $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION journal.stamp_created_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.created_at := now();  -- entries cannot be backdated
          RETURN NEW;
        END $$;
        """
    )
    for table in TABLES:
        op.execute(
            f"CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION journal.forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION journal.forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER stamp_created_at BEFORE INSERT ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION journal.stamp_created_at()"
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("watchlists", schema="journal")
    op.drop_table("entries", schema="journal")
    op.drop_table("reviews", schema="journal")
    op.drop_table("theses", schema="journal")
    op.execute("DROP FUNCTION journal.stamp_created_at()")
    op.execute("DROP FUNCTION journal.forbid_mutation()")
