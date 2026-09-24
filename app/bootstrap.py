"""First-run administrator creation.

A fresh database has no accounts, and every endpoint needs one — so there has to
be a way in. Setting ``BOOTSTRAP_ADMIN_EMAIL`` and ``BOOTSTRAP_ADMIN_PASSWORD``
creates an administrator on startup, but only while no users exist at all: once
anyone has an account the variables do nothing, so leaving them set in an
environment file cannot silently re-create or resurrect an account later.

For an interactive setup, prefer ``python -m scripts.create_admin``.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app import security
from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.reference import Role
from app.schemas import MIN_PASSWORD_LENGTH

logger = logging.getLogger("app.bootstrap")


def bootstrap_admin() -> bool:
    """Create the first administrator from the environment. Returns whether it did."""
    email = settings.bootstrap_admin_email.strip().casefold()
    password = settings.bootstrap_admin_password

    with SessionLocal() as db:
        already_populated = db.scalar(select(User).limit(1)) is not None

        if already_populated:
            if email or password:
                logger.info(
                    "BOOTSTRAP_ADMIN_* ignored: the database already has accounts."
                )
            return False

        if not email or not password:
            logger.warning(
                "No accounts exist yet. Create the first administrator with "
                "'python -m scripts.create_admin', or set BOOTSTRAP_ADMIN_EMAIL "
                "and BOOTSTRAP_ADMIN_PASSWORD."
            )
            return False

        if len(password) < MIN_PASSWORD_LENGTH:
            logger.error(
                "BOOTSTRAP_ADMIN_PASSWORD is shorter than %d characters; "
                "no administrator was created.",
                MIN_PASSWORD_LENGTH,
            )
            return False

        db.add(
            User(
                email=email,
                full_name="Administrator",
                password_hash=security.hash_password(password),
                role=Role.ADMIN,
            )
        )
        db.commit()
        logger.warning(
            "Created the first administrator (%s) from the environment. "
            "Sign in and change this password.",
            email,
        )
        return True
