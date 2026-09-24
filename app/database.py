"""Database engine, session factory and the declarative base."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(url: str) -> dict:
    # SQLite needs check_same_thread disabled so the FastAPI threadpool can
    # hand a connection to whichever worker thread serves the request.
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """SQLite ignores foreign keys unless they are switched on per connection.

    The test is on the connection itself, not on the module-level ``engine``.
    The listener is registered against every ``Engine`` in the process, and a
    process can hold more than one: asking the module engine what dialect this
    connection speaks gets the wrong answer as soon as they differ, and sends
    ``PRAGMA`` to a server that has never heard of it.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Bring the database up to the latest migration.

    Imported late and called through :mod:`app.migrate` so this module stays
    free of an Alembic dependency at import time.
    """
    from app import models  # noqa: F401  (registers the mappers)
    from app.migrate import upgrade_to_head

    upgrade_to_head()
