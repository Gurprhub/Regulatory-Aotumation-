"""Create any missing tables, then exit.

Run once before starting the web workers. Doing it here rather than letting
several workers race to create the same tables on first boot is the difference
between a clean start and one worker dying on a duplicate-table error.

It is safe to run on every deploy: existing tables are left alone.
"""

from __future__ import annotations

import logging

from app.bootstrap import bootstrap_admin
from app.database import init_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    init_db()
    logging.getLogger("app.init_db").info("Schema is up to date.")
    # Creates the first administrator only while no accounts exist at all.
    bootstrap_admin()


if __name__ == "__main__":
    main()
