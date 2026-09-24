"""Bring the database up to the latest migration, then exit.

Run once before starting the web workers. Doing it here rather than letting
several workers race is the difference between a clean start and one worker
dying part-way through a migration.

Safe on every deploy. A database created before Alembic was introduced is
stamped at the baseline rather than rebuilt, so its data survives — see
:mod:`app.migrate`.
"""

from __future__ import annotations

import logging

from app.bootstrap import bootstrap_admin
from app.database import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    init_db()  # app.migrate logs what it did
    # Creates the first administrator only while no accounts exist at all.
    bootstrap_admin()


if __name__ == "__main__":
    main()
