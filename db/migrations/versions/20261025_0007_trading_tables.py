"""trading strategies, promotions, orders, fills, snapshots, reconciliations, state,
heartbeats; decision records append-only (phase 7, ADR 0015).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25 21:00:00+00:00

"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = (
    "trading.promotions",
    "trading.orders",
    "trading.fills",
    "trading.positions_snapshot",
    "trading.reconciliations",
    "trading.trading_state",
)


def _created() -> sa.Column[datetime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _strategy_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["strategy_id"], ["trading.strategies.id"], name=op.f(f"fk_{table}_strategy_id_strategies")
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "strategies",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("signal", sa.Text(), nullable=False),
        sa.Column("params", sa.Text(), server_default="{}", nullable=False),
        sa.Column("instrument", sa.Text(), nullable=True),
        sa.Column("fx", sa.Text(), nullable=True),
        sa.Column("costs", sa.Text(), nullable=False),
        sa.Column("capital", sa.Numeric(), nullable=False),
        _created(),
        sa.CheckConstraint(
            "name ~ '^[a-z0-9][a-z0-9_-]*$'", name=op.f("ck_strategies_name_format")
        ),
        sa.CheckConstraint(
            "(instrument IS NULL) <> (fx IS NULL)", name=op.f("ck_strategies_one_market")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_strategies")),
        sa.UniqueConstraint("name", name=op.f("uq_strategies_name")),
        schema="trading",
    )
    op.create_table(
        "promotions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), server_default="", nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column("owner_confirmed", sa.Boolean(), server_default="false", nullable=False),
        _created(),
        sa.CheckConstraint(
            "status IN ('research', 'backtested', 'paper', 'approved_live', 'retired')",
            name=op.f("ck_promotions_status"),
        ),
        sa.CheckConstraint(
            "status <> 'approved_live' OR owner_confirmed",
            name=op.f("ck_promotions_live_needs_owner"),
        ),
        _strategy_fk("promotions"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotions")),
        schema="trading",
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reasons", sa.Text(), server_default="", nullable=False),
        _created(),
        _strategy_fk("orders"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
        schema="trading",
    )
    op.create_table(
        "fills",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("fee", sa.Numeric(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["order_id"], ["trading.orders.id"], name=op.f("fk_fills_order_id_orders")
        ),
        _strategy_fk("fills"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fills")),
        sa.UniqueConstraint("order_id", name=op.f("uq_fills_order_id")),
        schema="trading",
    )
    op.create_table(
        "positions_snapshot",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("cash", sa.Numeric(), nullable=False),
        sa.Column("equity", sa.Numeric(), nullable=False),
        sa.Column("pending_target", sa.Float(), nullable=True),
        _created(),
        _strategy_fk("positions_snapshot"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_positions_snapshot")),
        sa.UniqueConstraint("strategy_id", "bar_date", name="uq_positions_snapshot_day"),
        schema="trading",
    )
    op.create_table(
        "reconciliations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), nullable=False),
        sa.Column("bar_date", sa.Date(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("detail", sa.Text(), server_default="", nullable=False),
        _created(),
        _strategy_fk("reconciliations"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliations")),
        schema="trading",
    )
    op.create_table(
        "trading_state",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("halted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trading_state")),
        schema="trading",
    )
    op.create_table(
        "heartbeats",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("component", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), server_default="", nullable=False),
        _created(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_heartbeats")),
        schema="trading",
    )
    op.execute(
        """
        CREATE FUNCTION trading.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'qp: %.% is append-only (% rejected)',
            TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
        END $$;
        """
    )
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION trading.forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION trading.forbid_mutation()"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in (
        "heartbeats",
        "trading_state",
        "reconciliations",
        "positions_snapshot",
        "fills",
        "orders",
        "promotions",
        "strategies",
    ):
        op.drop_table(table, schema="trading")
    op.execute("DROP FUNCTION trading.forbid_mutation()")
