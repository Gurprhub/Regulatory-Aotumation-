"""Running schema migrations from inside the application.

``upgrade_to_head`` is what the container calls before starting its workers. It
handles three cases, and the third is the one that matters:

1. **A fresh database** — every migration runs, creating the schema.
2. **A database already under Alembic** — it upgrades from wherever it is.
3. **A database created before Alembic existed here**, by ``create_all``. Its
   tables are already correct but it has no ``alembic_version`` row, so a plain
   upgrade would try to ``CREATE TABLE`` over live tables and fail. It is
   stamped at the baseline instead, then upgraded — the schema is left alone
   and the data with it.

That third case is not hypothetical: every database this project created before
this module existed is in it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app import models  # noqa: F401  (populates Base.metadata — see below)
from app.config import settings
from app.database import Base, engine

# That models import is load-bearing, not tidiness: without it Base.metadata is
# empty, so the "does this database already hold our tables?" check answers no,
# the adoption path below is skipped, and the upgrade tries to CREATE TABLE over
# a live schema.

logger = logging.getLogger("app.migrate")

#: Absolute, so it resolves the same from any working directory.
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def alembic_config(url: str | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option(
        "script_location", str(ALEMBIC_INI.parent / "migrations")
    )
    config.set_main_option("sqlalchemy.url", url or settings.database_url)
    return config


def baseline_revision(config: Config | None = None) -> str:
    """The earliest revision, used to stamp a pre-Alembic database."""
    script = ScriptDirectory.from_config(config or alembic_config())
    bases = script.get_bases()
    if not bases:
        raise RuntimeError("no migrations found")
    return bases[0]


def _state(connection) -> tuple[bool, bool]:
    """Return (under Alembic already, has this project's tables)."""
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    versioned = "alembic_version" in tables
    ours = bool(tables & set(Base.metadata.tables) - {"alembic_version"})
    return versioned, ours


def upgrade_to_head(url: str | None = None) -> str:
    """Bring the database to the latest revision. Returns what it did.

    ``url`` defaults to the application's own database; passing one lets the
    tests, and any tooling, migrate somewhere else.
    """
    config = alembic_config(url)
    target = create_engine(url) if url else engine

    try:
        with target.connect() as connection:
            versioned, has_tables = _state(connection)
    finally:
        if url:
            target.dispose()

    if not versioned and has_tables:
        revision = baseline_revision(config)
        logger.warning(
            "Database has tables but no migration history — stamping it at the "
            "baseline (%s) rather than recreating the schema.",
            revision,
        )
        command.stamp(config, revision)
        outcome = "adopted"
    else:
        outcome = "upgraded" if versioned else "created"

    command.upgrade(config, "head")
    logger.info("Schema is at head (%s).", outcome)
    return outcome
