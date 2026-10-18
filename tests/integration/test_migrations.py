"""Alembic migrations against a real PostgreSQL 16, in throwaway databases."""

from collections.abc import Callable
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

import qp.ledger.models
import qp.reference.models
from qp.platform import SCHEMAS, Base, Database, create_db_engine
from qp.platform.health import Status, check_migrations, check_schemas
from qp.platform.settings import DbSettings

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
_ = qp.ledger.models


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    return type_ != "schema" or name in SCHEMAS


def test_up_down_up(scratch_db: DbSettings, alembic: Callable[..., None]) -> None:
    engine = create_db_engine(scratch_db)
    db = Database(engine)
    try:
        alembic(engine, "upgrade", "head")
        assert set(SCHEMAS) <= db.existing_schemas()
        assert check_schemas(db).status is Status.OK
        assert check_migrations(db, REPO / "alembic.ini").status is Status.OK

        with engine.connect() as conn:
            assert conn.execute(text("SHOW timezone")).scalar_one() == "UTC"

        alembic(engine, "downgrade", "base")
        assert not (set(SCHEMAS) & db.existing_schemas())
        assert check_migrations(db, REPO / "alembic.ini").status is Status.FAIL

        alembic(engine, "upgrade", "head")
        assert set(SCHEMAS) <= db.existing_schemas()
    finally:
        db.dispose()


def test_models_match_migrations(migrated_db: Database) -> None:
    """The ORM models and the Alembic migrations describe the same schema."""
    with migrated_db.engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "include_schemas": True,
                "include_name": include_name,
                "compare_server_default": True,
            },
        )
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_venues_are_seeded(migrated_db: Database) -> None:
    with migrated_db.engine.connect() as conn:
        codes = set(conn.execute(text("SELECT code FROM ref.venues")).scalars())
    assert {"BIST", "TEFAS", "NASDAQ", "NYSE", "ARCA", "BINANCE"} <= codes
