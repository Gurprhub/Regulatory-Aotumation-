"""Application settings, read from the environment with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


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


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    ``critical_days`` and ``warning_days`` drive the renewal alerting windows:
    an item expiring within ``critical_days`` is critical, within
    ``warning_days`` is a warning, and anything further out is simply valid.
    """

    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./regulatory.db")
    critical_days: int = _int_env("CRITICAL_DAYS", 30)
    warning_days: int = _int_env("WARNING_DAYS", 90)

    def __post_init__(self) -> None:
        if self.warning_days < self.critical_days:
            raise ValueError(
                "WARNING_DAYS must be greater than or equal to CRITICAL_DAYS "
                f"(got warning={self.warning_days}, critical={self.critical_days})"
            )


settings = Settings()
