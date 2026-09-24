"""Shared fixtures: an isolated in-memory database and signed-in clients per test.

Every endpoint requires authentication, so ``client`` is signed in as an editor
— the ordinary case the register tests care about. ``raw_client`` is the
unauthenticated one, and ``viewer_client`` / ``admin_client`` cover the other
roles; the authorisation rules themselves are tested in ``test_auth.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import security
from app.database import Base, get_session
from app.main import app
from app.models import User
from app.reference import Role

TODAY = date.today()

#: Shared by every seeded account; long enough to satisfy MIN_PASSWORD_LENGTH.
PASSWORD = "a-sufficiently-long-passphrase"


def days_from_now(days: int) -> str:
    """An ISO date ``days`` from today — keeps expectations stable over time."""
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture
def db_engine():
    # StaticPool keeps every connection pointed at the same in-memory database.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def session(db_engine) -> Iterator[Session]:
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    with factory() as session:
        yield session


@pytest.fixture
def client_factory(db_engine) -> Iterator[Callable[[], TestClient]]:
    """Build independent clients that share one database.

    Each call returns a client with its own cookie jar, so two roles can be
    signed in at once without one overwriting the other's session.
    """
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override_get_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    created: list[TestClient] = []

    def make() -> TestClient:
        client = TestClient(app)
        client.__enter__()
        created.append(client)
        return client

    try:
        yield make
    finally:
        for client in created:
            client.__exit__(None, None, None)
        app.dependency_overrides.clear()


@pytest.fixture
def raw_client(client_factory) -> TestClient:
    """A client with nobody signed in."""
    return client_factory()


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def make_user(
    db_engine,
    *,
    email: str,
    role: Role,
    is_active: bool = True,
    password: str = PASSWORD,
) -> int:
    factory = sessionmaker(bind=db_engine)
    with factory() as session:
        user = User(
            email=email,
            full_name=email.split("@")[0].title(),
            role=role,
            is_active=is_active,
            password_hash=security.hash_password(password),
        )
        session.add(user)
        session.commit()
        return user.id


def sign_in(client: TestClient, email: str, password: str = PASSWORD) -> None:
    """Sign the client in; the session cookie then sticks to it."""
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


@pytest.fixture
def admin_id(db_engine) -> int:
    return make_user(db_engine, email="admin@example.com", role=Role.ADMIN)


@pytest.fixture
def editor_id(db_engine) -> int:
    return make_user(db_engine, email="editor@example.com", role=Role.EDITOR)


@pytest.fixture
def viewer_id(db_engine) -> int:
    return make_user(db_engine, email="viewer@example.com", role=Role.VIEWER)


@pytest.fixture
def client(client_factory, editor_id: int) -> TestClient:
    """The default client: signed in as an editor."""
    client = client_factory()
    sign_in(client, "editor@example.com")
    return client


@pytest.fixture
def editor_client(client: TestClient) -> TestClient:
    return client


@pytest.fixture
def admin_client(client_factory, admin_id: int) -> TestClient:
    client = client_factory()
    sign_in(client, "admin@example.com")
    return client


@pytest.fixture
def viewer_client(client_factory, viewer_id: int) -> TestClient:
    client = client_factory()
    sign_in(client, "viewer@example.com")
    return client


# --------------------------------------------------------------------------- #
# Register fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def product(client: TestClient) -> dict:
    response = client.post(
        "/api/products",
        json={
            "name": "Imidacloprid 17.8% SL",
            "brand_name": "Scimida",
            "active_ingredient": "Imidacloprid",
            "concentration": "17.8% w/w",
            "cas_number": "138261-41-3",
            "category": "insecticide",
            "formulation_type": "SL",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def registration(client: TestClient, product: dict) -> dict:
    response = client.post(
        "/api/registrations",
        json={
            "product_id": product["id"],
            "registration_number": "CIR-123456/2023-Imidacloprid(SL)",
            "section": "9(3)",
            "purpose": "manufacture",
            "registrant_name": "Scimplify Agro Pvt Ltd",
            "issue_date": days_from_now(-400),
            "valid_from": days_from_now(-400),
            "valid_until": days_from_now(300),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
