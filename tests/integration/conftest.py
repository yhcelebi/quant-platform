"""Integration fixtures: a throwaway PostgreSQL database per test, migrated to head.

Never touches the configured database. Skipped without a server unless QP_REQUIRE_DB=1.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text

from qp.platform import Database, create_db_engine, database_url, load_settings
from qp.platform.settings import DbSettings

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


@pytest.fixture
def alembic() -> Callable[..., None]:
    """Run an Alembic command (e.g. "upgrade", "head") on an engine."""
    return _alembic


@pytest.fixture
def migrated_db(scratch_db: DbSettings) -> Iterator[Database]:
    engine = create_db_engine(scratch_db)
    _alembic(engine, "upgrade", "head")
    db = Database(engine)
    try:
        yield db
    finally:
        db.dispose()
