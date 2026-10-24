"""news sources, items, entity links and tags (phase 4).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25 15:00:00+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sources",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), server_default="", nullable=False),
        sa.Column("public_institution", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_sources")),
        schema="news",
    )
    op.create_table(
        "items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("guid", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_norm", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("categories", sa.Text(), server_default="", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("known_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_ref", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "known_time >= published_at AND known_time >= fetched_at", name=op.f("ck_items_known")
        ),
        sa.CheckConstraint("length(summary) <= 500", name=op.f("ck_items_summary_short")),
        sa.ForeignKeyConstraint(
            ["source_code"], ["news.sources.code"], name=op.f("fk_items_source_code_sources")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
        sa.UniqueConstraint("url", name=op.f("uq_items_url")),
        schema="news",
    )
    op.create_index("ix_items_source_title", "items", ["source_code", "title_norm"], schema="news")
    op.create_index("ix_items_known_time", "items", ["known_time"], schema="news")
    op.create_table(
        "item_entities",
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("matched", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('instrument', 'currency', 'institution')", name=op.f("ck_item_entities_kind")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["news.items.id"], name=op.f("fk_item_entities_item_id_items")
        ),
        sa.PrimaryKeyConstraint("item_id", "kind", "key", name=op.f("pk_item_entities")),
        schema="news",
    )
    op.create_table(
        "item_tags",
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["news.items.id"], name=op.f("fk_item_tags_item_id_items")
        ),
        sa.PrimaryKeyConstraint("item_id", "tag", name=op.f("pk_item_tags")),
        schema="news",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("item_tags", schema="news")
    op.drop_table("item_entities", schema="news")
    op.drop_index("ix_items_known_time", table_name="items", schema="news")
    op.drop_index("ix_items_source_title", table_name="items", schema="news")
    op.drop_table("items", schema="news")
    op.drop_table("sources", schema="news")
