"""Migration tests.

The one that matters most is ``test_migrations_match_the_models``: without it,
the models and the migrations drift apart silently, and the first sign is a
production database whose schema no longer matches the code.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app import security
from app.database import Base
from app.migrate import alembic_config, baseline_revision, upgrade_to_head
from app.models import Product, User
from app.reference import FormulationType, ProductCategory, Role


@pytest.fixture
def scratch_url(tmp_path) -> Iterator[str]:
    """A file database of its own — migrations cannot run on :memory:."""
    yield f"sqlite:///{tmp_path}/scratch.db"


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


class TestFreshDatabase:
    def test_upgrade_creates_the_schema(self, scratch_url: str) -> None:
        assert upgrade_to_head(scratch_url) == "created"
        tables = _tables(scratch_url)
        assert "alembic_version" in tables
        assert set(Base.metadata.tables) <= tables

    def test_upgrade_is_idempotent(self, scratch_url: str) -> None:
        upgrade_to_head(scratch_url)
        before = _tables(scratch_url)
        assert upgrade_to_head(scratch_url) == "upgraded"
        assert _tables(scratch_url) == before

    def test_migrations_match_the_models(self, scratch_url: str) -> None:
        """A migrated database must match the models exactly.

        If this fails, someone changed a model without a migration: run
        ``alembic revision --autogenerate`` and commit the result.
        """
        upgrade_to_head(scratch_url)
        engine = create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(
                    connection,
                    opts={
                        "compare_type": True,
                        "compare_server_default": True,
                        "include_object": lambda obj, name, type_, reflected, compare_to: not (
                            type_ == "table" and name == "alembic_version"
                        ),
                    },
                )
                differences = compare_metadata(context, Base.metadata)
        finally:
            engine.dispose()

        assert differences == [], (
            "models and migrations have drifted; "
            "run 'alembic revision --autogenerate -m \"...\"' and commit it: "
            f"{differences}"
        )

    def test_downgrade_unwinds_the_baseline(self, scratch_url: str) -> None:
        upgrade_to_head(scratch_url)
        config = alembic_config(scratch_url)
        command.downgrade(config, "base")

        remaining = _tables(scratch_url) - {"alembic_version"}
        assert remaining == set(), remaining


class TestAdoptingAnExistingDatabase:
    """A database built before Alembic must be stamped, never recreated."""

    @staticmethod
    def _build_pre_alembic(url: str) -> None:
        engine = create_engine(url)
        try:
            Base.metadata.create_all(engine)
            with sessionmaker(bind=engine)() as db:
                db.info["audit_disabled"] = True
                db.add(
                    User(
                        email="incumbent@example.com",
                        full_name="Incumbent",
                        role=Role.ADMIN,
                        password_hash=security.hash_password("a-long-passphrase"),
                    )
                )
                db.add(
                    Product(
                        name="Existing 10% EC",
                        active_ingredient="Something",
                        category=ProductCategory.INSECTICIDE,
                        formulation_type=FormulationType.EC,
                    )
                )
                db.commit()
        finally:
            engine.dispose()

    def test_it_is_adopted_not_rebuilt(self, scratch_url: str) -> None:
        self._build_pre_alembic(scratch_url)
        assert "alembic_version" not in _tables(scratch_url)

        assert upgrade_to_head(scratch_url) == "adopted"

    def test_the_data_survives(self, scratch_url: str) -> None:
        self._build_pre_alembic(scratch_url)
        upgrade_to_head(scratch_url)

        engine = create_engine(scratch_url)
        try:
            with sessionmaker(bind=engine)() as db:
                assert db.scalar(select(User.email)) == "incumbent@example.com"
                assert db.scalar(select(Product.name)) == "Existing 10% EC"
        finally:
            engine.dispose()

    def test_it_is_stamped_at_the_baseline(self, scratch_url: str) -> None:
        self._build_pre_alembic(scratch_url)
        upgrade_to_head(scratch_url)

        engine = create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                stamped = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
        finally:
            engine.dispose()
        assert stamped == baseline_revision()

    def test_adopting_twice_is_harmless(self, scratch_url: str) -> None:
        self._build_pre_alembic(scratch_url)
        assert upgrade_to_head(scratch_url) == "adopted"
        assert upgrade_to_head(scratch_url) == "upgraded"

    def test_an_adopted_database_also_matches_the_models(self, scratch_url: str) -> None:
        self._build_pre_alembic(scratch_url)
        upgrade_to_head(scratch_url)

        engine = create_engine(scratch_url)
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(
                    connection,
                    opts={
                        "compare_type": True,
                        "include_object": lambda obj, name, type_, reflected, compare_to: not (
                            type_ == "table" and name == "alembic_version"
                        ),
                    },
                )
                differences = compare_metadata(context, Base.metadata)
        finally:
            engine.dispose()
        assert differences == []


class TestMigrationHistory:
    def test_there_is_exactly_one_root(self) -> None:
        """Two roots mean two histories, and an ambiguous upgrade path."""
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(alembic_config())
        assert len(script.get_bases()) == 1

    def test_every_revision_is_reachable_from_head(self) -> None:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(alembic_config())
        heads = script.get_heads()
        assert len(heads) == 1, f"multiple heads need merging: {heads}"

        walked = {revision.revision for revision in script.walk_revisions()}
        assert walked, "no migrations found"
