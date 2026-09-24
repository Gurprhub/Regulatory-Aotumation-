"""Settings read from the environment."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.config import normalize_database_url


class TestNormalizeDatabaseUrl:
    @pytest.mark.parametrize(
        "given",
        [
            # Render, Fly and most hosts hand out one of these two forms.
            "postgres://app:secret@db.internal:5432/regulatory",
            "postgresql://app:secret@db.internal:5432/regulatory",
        ],
    )
    def test_bare_postgres_urls_use_psycopg3(self, given: str) -> None:
        url = normalize_database_url(given)
        assert url == "postgresql+psycopg://app:secret@db.internal:5432/regulatory"
        assert make_url(url).get_driver_name() == "psycopg"

    @pytest.mark.parametrize(
        "given",
        [
            "postgresql+psycopg://app:secret@db/regulatory",
            "postgresql+psycopg2://app:secret@db/regulatory",
            "sqlite:///./regulatory.db",
            "sqlite://",
        ],
    )
    def test_urls_that_name_a_driver_are_left_alone(self, given: str) -> None:
        assert normalize_database_url(given) == given

    def test_surrounding_whitespace_is_dropped(self) -> None:
        # A value pasted into a dashboard often carries a trailing newline.
        assert (
            normalize_database_url("  postgresql://app:secret@db/regulatory\n")
            == "postgresql+psycopg://app:secret@db/regulatory"
        )
