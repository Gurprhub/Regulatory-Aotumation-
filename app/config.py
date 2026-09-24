"""Application settings, read from the environment with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value}")
    return value


def normalize_database_url(url: str) -> str:
    """Point a bare PostgreSQL URL at the psycopg 3 driver this project ships.

    Hosted databases (Render, Heroku, Fly and most others) hand out URLs that
    start ``postgres://`` or ``postgresql://``. SQLAlchemy reads the first as
    an unknown dialect and the second as psycopg2, which is not installed, so
    either would fail at startup. A URL that already names a driver is left
    alone.
    """
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    ``critical_days`` and ``warning_days`` drive the renewal alerting windows:
    an item expiring within ``critical_days`` is critical, within
    ``warning_days`` is a warning, and anything further out is simply valid.
    """

    database_url: str = normalize_database_url(
        os.environ.get("DATABASE_URL", "sqlite:///./regulatory.db")
    )
    critical_days: int = _int_env("CRITICAL_DAYS", 30)
    warning_days: int = _int_env("WARNING_DAYS", 90)

    # --- authentication ---------------------------------------------------- #
    #: How long a browser session stays valid, in hours.
    session_hours: int = _int_env("SESSION_HOURS", 12)
    #: Send the session cookie only over HTTPS. Must be on in production; off by
    #: default so the app still works over plain http on localhost.
    session_cookie_secure: bool = _bool_env("SESSION_COOKIE_SECURE", False)
    #: Consecutive failed sign-ins before an account is temporarily locked.
    max_failed_logins: int = _int_env("MAX_FAILED_LOGINS", 10)
    #: How long that lock lasts, in minutes.
    lockout_minutes: int = _int_env("LOCKOUT_MINUTES", 15)
    #: Optional first-run administrator, created on startup when no users exist.
    bootstrap_admin_email: str = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "")
    bootstrap_admin_password: str = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")

    def __post_init__(self) -> None:
        if self.warning_days < self.critical_days:
            raise ValueError(
                "WARNING_DAYS must be greater than or equal to CRITICAL_DAYS "
                f"(got warning={self.warning_days}, critical={self.critical_days})"
            )
        if self.session_hours < 1:
            raise ValueError("SESSION_HOURS must be at least 1")
        if self.max_failed_logins < 1:
            raise ValueError("MAX_FAILED_LOGINS must be at least 1")


settings = Settings()
