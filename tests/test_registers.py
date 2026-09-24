"""API tests for the four compliance registers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import days_from_now


# --------------------------------------------------------------------------- #
# Registrations
# --------------------------------------------------------------------------- #
def test_registration_exposes_derived_compliance_fields(registration: dict) -> None:
    assert registration["compliance_state"] == "valid"
    assert registration["days_remaining"] == 300
    assert registration["product_name"] == "Imidacloprid 17.8% SL"


def test_registration_for_unknown_product_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/registrations",
        json={
            "product_id": 4242,
            "registration_number": "CIR-1/2026",
            "section": "9(3)",
            "registrant_name": "Acme",
        },
    )
    assert response.status_code == 404


def test_duplicate_registration_number_conflicts(
    client: TestClient, product: dict, registration: dict
) -> None:
    response = client.post(
        "/api/registrations",
        json={
            "product_id": product["id"],
            "registration_number": registration["registration_number"],
            "section": "9(4)",
            "registrant_name": "Someone Else",
        },
    )
    assert response.status_code == 409


def test_validity_window_is_enforced_on_create(client: TestClient, product: dict) -> None:
    response = client.post(
        "/api/registrations",
        json={
            "product_id": product["id"],
            "registration_number": "CIR-9/2026",
            "section": "9(3)",
            "registrant_name": "Acme",
            "valid_from": days_from_now(100),
            "valid_until": days_from_now(50),
        },
    )
    assert response.status_code == 422
    assert "valid_until" in response.text


def test_validity_window_is_enforced_on_partial_update(
    client: TestClient, registration: dict
) -> None:
    """A PATCH that moves only one end of the window must still be checked."""
    response = client.patch(
        f"/api/registrations/{registration['id']}",
        json={"valid_until": days_from_now(-500)},
    )
    assert response.status_code == 422


def test_perpetual_registration_has_no_countdown(
    client: TestClient, product: dict
) -> None:
    response = client.post(
        "/api/registrations",
        json={
            "product_id": product["id"],
            "registration_number": "CIR-PERP/2026",
            "section": "9(3)",
            "registrant_name": "Acme",
            "valid_from": days_from_now(-30),
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["valid_until"] is None
    assert body["days_remaining"] is None
    assert body["compliance_state"] == "valid"


def test_suspension_marks_registration_non_compliant(
    client: TestClient, registration: dict
) -> None:
    response = client.patch(
        f"/api/registrations/{registration['id']}", json={"status": "suspended"}
    )
    assert response.json()["compliance_state"] == "non_compliant"


def test_expiring_within_filter(client: TestClient, product: dict, registration: dict) -> None:
    soon = client.post(
        "/api/registrations",
        json={
            "product_id": product["id"],
            "registration_number": "CIR-SOON/2026",
            "section": "9(4)",
            "registrant_name": "Acme",
            "valid_until": days_from_now(20),
        },
    )
    assert soon.status_code == 201

    filtered = client.get("/api/registrations", params={"expiring_within": 45})
    assert [item["registration_number"] for item in filtered.json()] == ["CIR-SOON/2026"]

    by_state = client.get("/api/registrations", params={"compliance_state": "critical"})
    assert [item["registration_number"] for item in by_state.json()] == ["CIR-SOON/2026"]


# --------------------------------------------------------------------------- #
# State sale permissions
# --------------------------------------------------------------------------- #
def _permission_payload(product_id: int, **overrides) -> dict:
    payload = {
        "product_id": product_id,
        "state": "Maharashtra",
        "permission_number": "MH/SP/2026/0001",
        "licensing_authority": "Commissioner of Agriculture, Maharashtra",
        "valid_from": days_from_now(-100),
        "valid_until": days_from_now(25),
    }
    payload.update(overrides)
    return payload


def test_sale_permission_state_is_normalised(client: TestClient, product: dict) -> None:
    response = client.post(
        "/api/sale-permissions",
        json=_permission_payload(product["id"], state="  tamil nadu "),
    )
    assert response.status_code == 201
    assert response.json()["state"] == "Tamil Nadu"


def test_unknown_state_is_rejected(client: TestClient, product: dict) -> None:
    response = client.post(
        "/api/sale-permissions", json=_permission_payload(product["id"], state="Bavaria")
    )
    assert response.status_code == 422
    assert "Unknown state" in response.text


def test_sale_permission_links_to_registration(
    client: TestClient, product: dict, registration: dict
) -> None:
    response = client.post(
        "/api/sale-permissions",
        json=_permission_payload(product["id"], registration_id=registration["id"]),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["registration_id"] == registration["id"]
    assert body["compliance_state"] == "critical"
    assert body["days_remaining"] == 25


def test_sale_permission_unknown_registration_is_404(
    client: TestClient, product: dict
) -> None:
    response = client.post(
        "/api/sale-permissions",
        json=_permission_payload(product["id"], registration_id=777),
    )
    assert response.status_code == 404


def test_duplicate_permission_number_in_same_state_conflicts(
    client: TestClient, product: dict
) -> None:
    assert (
        client.post(
            "/api/sale-permissions", json=_permission_payload(product["id"])
        ).status_code
        == 201
    )
    duplicate = client.post(
        "/api/sale-permissions", json=_permission_payload(product["id"])
    )
    assert duplicate.status_code == 409


def test_sale_permissions_filtered_by_state(client: TestClient, product: dict) -> None:
    client.post("/api/sale-permissions", json=_permission_payload(product["id"]))
    client.post(
        "/api/sale-permissions",
        json=_permission_payload(
            product["id"], state="Punjab", permission_number="PB/SP/2026/0007"
        ),
    )

    punjab = client.get("/api/sale-permissions", params={"state": "punjab"})
    assert [item["permission_number"] for item in punjab.json()] == ["PB/SP/2026/0007"]


# --------------------------------------------------------------------------- #
# Licences
# --------------------------------------------------------------------------- #
def _licence_payload(**overrides) -> dict:
    payload = {
        "licence_number": "GJ/MFG/2026/019",
        "licence_type": "manufacture",
        "holder_name": "Scimplify Agro Pvt Ltd",
        "site_name": "Dahej Plant",
        "state": "Gujarat",
        "issuing_authority": "Directorate of Agriculture, Gujarat",
        "valid_from": days_from_now(-200),
        "valid_until": days_from_now(150),
    }
    payload.update(overrides)
    return payload


def test_licence_covers_products(client: TestClient, product: dict) -> None:
    response = client.post(
        "/api/licences", json=_licence_payload(product_ids=[product["id"]])
    )
    assert response.status_code == 201, response.text
    assert response.json()["product_ids"] == [product["id"]]


def test_licence_with_unknown_product_is_404(client: TestClient) -> None:
    response = client.post("/api/licences", json=_licence_payload(product_ids=[5150]))
    assert response.status_code == 404


def test_licence_products_can_be_replaced(client: TestClient, product: dict) -> None:
    created = client.post("/api/licences", json=_licence_payload()).json()
    assert created["product_ids"] == []

    updated = client.patch(
        f"/api/licences/{created['id']}", json={"product_ids": [product["id"]]}
    )
    assert updated.json()["product_ids"] == [product["id"]]

    cleared = client.patch(f"/api/licences/{created['id']}", json={"product_ids": []})
    assert cleared.json()["product_ids"] == []


def test_licences_filtered_by_covered_product(client: TestClient, product: dict) -> None:
    client.post("/api/licences", json=_licence_payload(product_ids=[product["id"]]))
    client.post(
        "/api/licences",
        json=_licence_payload(
            licence_number="PB/SALE/2026/44", licence_type="sale", state="Punjab"
        ),
    )

    covering = client.get("/api/licences", params={"product_id": product["id"]})
    assert [item["licence_number"] for item in covering.json()] == ["GJ/MFG/2026/019"]

    by_type = client.get("/api/licences", params={"licence_type": "sale"})
    assert [item["licence_number"] for item in by_type.json()] == ["PB/SALE/2026/44"]


def test_duplicate_licence_number_in_state_conflicts(client: TestClient) -> None:
    assert client.post("/api/licences", json=_licence_payload()).status_code == 201
    assert client.post("/api/licences", json=_licence_payload()).status_code == 409
    # The same number in a different state is a different licence.
    assert (
        client.post("/api/licences", json=_licence_payload(state="Punjab")).status_code
        == 201
    )


# --------------------------------------------------------------------------- #
# Label approvals
# --------------------------------------------------------------------------- #
def test_label_approval_lifecycle(
    client: TestClient, product: dict, registration: dict
) -> None:
    created = client.post(
        "/api/label-approvals",
        json={
            "product_id": product["id"],
            "registration_id": registration["id"],
            "approval_number": "LBL/2026/0042",
            "label_version": "2.1",
            "leaflet_version": "2.0",
            "languages": "English, Hindi, Marathi",
            "issue_date": days_from_now(-60),
            "valid_from": days_from_now(-60),
            "valid_until": days_from_now(-1),
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["compliance_state"] == "expired"
    assert body["days_remaining"] == -1

    renewed = client.patch(
        f"/api/label-approvals/{body['id']}",
        json={"valid_until": days_from_now(365), "status": "active"},
    )
    assert renewed.json()["compliance_state"] == "valid"

    assert client.delete(f"/api/label-approvals/{body['id']}").status_code == 204
    assert client.get(f"/api/label-approvals/{body['id']}").status_code == 404


def test_duplicate_label_version_conflicts(client: TestClient, product: dict) -> None:
    payload = {
        "product_id": product["id"],
        "approval_number": "LBL/2026/0099",
        "label_version": "1.0",
    }
    assert client.post("/api/label-approvals", json=payload).status_code == 201
    assert client.post("/api/label-approvals", json=payload).status_code == 409
    # A new version of the same approval is allowed.
    assert (
        client.post(
            "/api/label-approvals", json={**payload, "label_version": "1.1"}
        ).status_code
        == 201
    )


def test_unknown_field_is_rejected(client: TestClient, product: dict) -> None:
    """A misspelt date field must fail loudly, not be dropped."""
    response = client.post(
        "/api/label-approvals",
        json={
            "product_id": product["id"],
            "approval_number": "LBL/2026/0500",
            "approval_date": days_from_now(-10),  # not a field; issue_date is
        },
    )
    assert response.status_code == 422
    assert "approval_date" in response.text
