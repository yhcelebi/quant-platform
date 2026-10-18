"""Alembic environment for qp.

The database URL comes from qp settings (configs/ + env/.env), never from alembic.ini.
Tests may pass an open connection via ``config.attributes["connection"]``.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from qp.platform.db import create_db_engine, database_url
from qp.platform.settings import load_settings

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name)

# Phase 1+ sets this to the qp ORM MetaData for autogenerate support.
target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection (``alembic upgrade head --sql``)."""
    url = database_url(load_settings().db).render_as_string(hide_password=True)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_db_engine(load_settings().db)
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
