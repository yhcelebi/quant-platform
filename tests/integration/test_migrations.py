"""Alembic baseline against a real PostgreSQL 16.

Runs in a throwaway database (created and dropped here), never the configured one, so it
cannot downgrade or damage real data. Skipped when no server is reachable, unless
QP_REQUIRE_DB=1 (set in CI), in which case an unreachable server is a failure.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from qp.platform import SCHEMAS, Database, create_db_engine, database_url, load_settings
from qp.platform.health import Status, check_migrations, check_schemas
from qp.platform.settings import DbSettings

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
# Read at import (collection), before the autouse fixture strips QP_* variables.
REQUIRE_DB = os.environ.get("QP_REQUIRE_DB") == "1"


@pytest.fixture
def scratch_db(host_db_env: dict[str, str]) -> Iterator[DbSettings]:
    base = load_settings(config_dir=REPO / "configs", env_file=REPO / ".env").db
    admin_url = database_url(base.model_copy(update={"name": "postgres"}))
    admin = create_engine(
        admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )
    try:
        with admin.connect() as conn:
            version = conn.execute(text("SHOW server_version_num")).scalar_one()
    except Exception as exc:  # no server: skip (or fail in CI), and say why
        admin.dispose()
        if REQUIRE_DB:
            pytest.fail(f"QP_REQUIRE_DB=1 but PostgreSQL is not reachable: {exc}")
        pytest.skip(f"PostgreSQL not reachable at {base.host}:{base.port}: {type(exc).__name__}")
    assert int(version) // 10000 == 16, f"expected PostgreSQL 16, got {version}"
    name = f"qp_test_{uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield base.model_copy(update={"name": name})
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _alembic(engine: Engine, *args: str) -> None:
    cfg = Config(str(REPO / "alembic.ini"))
    cfg.attributes["skip_logging_config"] = True
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        getattr(command, args[0])(cfg, *args[1:])


def test_baseline_up_down_up(scratch_db: DbSettings) -> None:
    engine = create_db_engine(scratch_db)
    db = Database(engine)
    try:
        _alembic(engine, "upgrade", "head")
        assert set(SCHEMAS) <= db.existing_schemas()
        assert check_schemas(db).status is Status.OK
        assert check_migrations(db, REPO / "alembic.ini").status is Status.OK

        with engine.connect() as conn:
            assert conn.execute(text("SHOW timezone")).scalar_one() == "UTC"

        _alembic(engine, "downgrade", "base")
        assert not (set(SCHEMAS) & db.existing_schemas())
        assert check_migrations(db, REPO / "alembic.ini").status is Status.FAIL

        _alembic(engine, "upgrade", "head")
        assert set(SCHEMAS) <= db.existing_schemas()
    finally:
        db.dispose()
