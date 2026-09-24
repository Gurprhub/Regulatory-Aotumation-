"""Dashboard, alert feed, CSV export and reference endpoint tests."""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from tests.conftest import days_from_now


@pytest.fixture
def populated(client: TestClient, product: dict, registration: dict) -> dict:
    """One item in each urgency band, spread across the registers."""
    product_id = product["id"]

    expired_permission = client.post(
        "/api/sale-permissions",
        json={
            "product_id": product_id,
            "state": "Punjab",
            "permission_number": "PB/SP/2025/0011",
            "valid_from": days_from_now(-400),
            "valid_until": days_from_now(-15),
        },
    )
    assert expired_permission.status_code == 201, expired_permission.text

    critical_licence = client.post(
        "/api/licences",
        json={
            "licence_number": "GJ/MFG/2026/019",
            "licence_type": "manufacture",
            "holder_name": "Scimplify Agro Pvt Ltd",
            "site_name": "Dahej Plant",
            "state": "Gujarat",
            "valid_until": days_from_now(10),
            "product_ids": [product_id],
        },
    )
    assert critical_licence.status_code == 201, critical_licence.text

    warning_label = client.post(
        "/api/label-approvals",
        json={
            "product_id": product_id,
            "registration_id": registration["id"],
            "approval_number": "LBL/2026/0042",
            "label_version": "2.1",
            "valid_until": days_from_now(60),
        },
    )
    assert warning_label.status_code == 201, warning_label.text

    suspended = client.post(
        "/api/sale-permissions",
        json={
            "product_id": product_id,
            "state": "Kerala",
            "permission_number": "KL/SP/2026/0003",
            "valid_until": days_from_now(800),
            "status": "suspended",
        },
    )
    assert suspended.status_code == 201, suspended.text

    return {
        "expired_permission": expired_permission.json(),
        "critical_licence": critical_licence.json(),
        "warning_label": warning_label.json(),
        "suspended_permission": suspended.json(),
        "registration": registration,
    }


def test_dashboard_counts_every_register(client: TestClient, populated: dict) -> None:
    body = client.get("/api/dashboard").json()

    assert body["critical_days"] == 30
    assert body["warning_days"] == 90
    assert body["totals"]["expired"] == 1
    assert body["totals"]["critical"] == 1
    assert body["totals"]["expiring_soon"] == 1
    assert body["totals"]["non_compliant"] == 1
    assert body["totals"]["valid"] == 1  # the registration, 300 days out

    registers = {entry["register_type"]: entry for entry in body["registers"]}
    assert set(registers) == {
        "registration",
        "sale_permission",
        "licence",
        "label_approval",
    }
    assert registers["sale_permission"]["total"] == 2
    assert registers["sale_permission"]["actionable"] == 2
    assert registers["registration"]["actionable"] == 0
    assert registers["licence"]["by_state"]["critical"] == 1


def test_dashboard_is_empty_on_a_fresh_database(client: TestClient) -> None:
    body = client.get("/api/dashboard").json()
    assert body["totals"] == {
        "valid": 0,
        "expiring_soon": 0,
        "critical": 0,
        "expired": 0,
        "not_yet_effective": 0,
        "non_compliant": 0,
    }
    assert body["upcoming"] == []
    assert [entry["total"] for entry in body["registers"]] == [0, 0, 0, 0]


def test_alerts_are_ordered_by_urgency(client: TestClient, populated: dict) -> None:
    items = client.get("/api/alerts").json()

    assert [item["compliance_state"] for item in items] == [
        "non_compliant",
        "expired",
        "critical",
        "expiring_soon",
    ]
    # The healthy registration is outside the default 90-day horizon.
    assert all(item["compliance_state"] != "valid" for item in items)

    first = items[0]
    assert first["register_type"] == "sale_permission"
    assert first["register_label"] == "State sale permission"
    assert first["reference_number"] == "KL/SP/2026/0003"


def test_alert_horizon_narrows_the_queue(client: TestClient, populated: dict) -> None:
    items = client.get("/api/alerts", params={"within_days": 14}).json()
    references = [item["reference_number"] for item in items]

    # Suspended and already-expired items always matter; the 60-day label does not.
    assert "KL/SP/2026/0003" in references
    assert "PB/SP/2025/0011" in references
    assert "GJ/MFG/2026/019" in references
    assert "LBL/2026/0042" not in references


def test_alerts_filtered_by_register_and_state(client: TestClient, populated: dict) -> None:
    licences = client.get("/api/alerts", params={"register": "licence"}).json()
    assert [item["reference_number"] for item in licences] == ["GJ/MFG/2026/019"]

    punjab = client.get("/api/alerts", params={"state": "Punjab"}).json()
    assert [item["reference_number"] for item in punjab] == ["PB/SP/2025/0011"]


def test_unknown_register_is_rejected(client: TestClient) -> None:
    response = client.get("/api/alerts", params={"register": "made-up"})
    assert response.status_code == 422
    assert "Unknown register" in response.text


def test_alerts_csv_export(client: TestClient, populated: dict) -> None:
    response = client.get("/api/alerts.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 4
    assert rows[0]["compliance_state"] == "non_compliant"
    assert rows[1]["reference_number"] == "PB/SP/2025/0011"
    assert rows[1]["days_remaining"] == "-15"
    assert rows[2]["state"] == "Gujarat"


def test_reference_vocabularies(client: TestClient) -> None:
    body = client.get("/api/reference").json()
    assert "Punjab" in body["states"]
    assert len(body["states"]) == 36
    assert "9(3)" in body["registration_sections"]
    assert "stock_and_sale" in body["licence_types"]
    assert body["thresholds"] == {"critical_days": 30, "warning_days": 90}
    assert {entry["key"] for entry in body["registers"]} == {
        "registration",
        "sale_permission",
        "licence",
        "label_approval",
    }


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["warning_days"] == 90


def test_dashboard_upcoming_is_limited(client: TestClient, populated: dict) -> None:
    body = client.get("/api/dashboard", params={"upcoming_limit": 2}).json()
    assert len(body["upcoming"]) == 2
    assert body["upcoming"][0]["compliance_state"] == "non_compliant"


def test_index_page_is_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
