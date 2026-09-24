"""Shared fixtures: an isolated in-memory database per test."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_session
from app.main import app

TODAY = date.today()


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
def client(db_engine) -> Iterator[TestClient]:
    factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override_get_session() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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
