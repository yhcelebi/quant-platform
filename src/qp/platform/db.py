"""Database engine and sessions (SQLAlchemy 2 + psycopg 3).

Every connection runs with ``timezone=UTC`` so timestamptz values round-trip as UTC.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, MetaData, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from qp.platform.settings import DbSettings

# Operational schemas, created by the Alembic baseline (db/migrations).
SCHEMAS: tuple[str, ...] = ("ref", "ledger", "trading", "news", "journal", "ops")

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every qp ORM model; one MetaData so cross-schema FKs resolve."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def database_url(db: DbSettings) -> URL:
    """SQLAlchemy URL. Its str() hides the password; never log render_as_string(False)."""
    return URL.create(
        drivername="postgresql+psycopg",
        username=db.user,
        password=db.password.get_secret_value() or None,
        host=db.host,
        port=db.port,
        database=db.name,
    )


def create_db_engine(db: DbSettings, *, echo: bool = False) -> Engine:
    return create_engine(
        database_url(db),
        echo=echo,
        pool_pre_ping=True,
        connect_args={"connect_timeout": db.connect_timeout_s, "options": "-c timezone=UTC"},
    )


class Database:
    """Owns an Engine and a session factory."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls, db: DbSettings) -> Database:
        return cls(create_db_engine(db))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A session in a transaction: commit on success, roll back on error."""
        with self._sessions() as session, session.begin():
            yield session

    def ping(self) -> None:
        """Raise if the database is unreachable."""
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def existing_schemas(self) -> set[str]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
            return {str(r[0]) for r in rows}

    def dispose(self) -> None:
        self.engine.dispose()
