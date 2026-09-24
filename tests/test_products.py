"""Product register API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

PAYLOAD = {
    "name": "Glyphosate 41% SL",
    "active_ingredient": "Glyphosate IPA salt",
    "category": "herbicide",
    "formulation_type": "SL",
}


def test_create_and_read_product(client: TestClient) -> None:
    created = client.post("/api/products", json=PAYLOAD)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Glyphosate 41% SL"
    assert body["category"] == "herbicide"

    fetched = client.get(f"/api/products/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_duplicate_name_and_formulation_conflicts(client: TestClient) -> None:
    assert client.post("/api/products", json=PAYLOAD).status_code == 201
    duplicate = client.post("/api/products", json=PAYLOAD)
    assert duplicate.status_code == 409
    assert "conflicts" in duplicate.json()["detail"]


def test_same_name_different_formulation_is_allowed(client: TestClient) -> None:
    assert client.post("/api/products", json=PAYLOAD).status_code == 201
    other = client.post("/api/products", json={**PAYLOAD, "formulation_type": "WG"})
    assert other.status_code == 201


def test_search_and_category_filters(client: TestClient, product: dict) -> None:
    client.post("/api/products", json=PAYLOAD)

    by_ingredient = client.get("/api/products", params={"q": "imidacloprid"})
    assert [item["id"] for item in by_ingredient.json()] == [product["id"]]

    by_category = client.get("/api/products", params={"category": "herbicide"})
    assert [item["name"] for item in by_category.json()] == ["Glyphosate 41% SL"]

    by_formulation = client.get("/api/products", params={"formulation_type": "WG"})
    assert by_formulation.json() == []


def test_update_product(client: TestClient, product: dict) -> None:
    response = client.patch(
        f"/api/products/{product['id']}", json={"brand_name": "Scimida Gold"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["brand_name"] == "Scimida Gold"
    # Untouched fields survive a partial update.
    assert body["active_ingredient"] == product["active_ingredient"]


def test_invalid_category_is_rejected(client: TestClient) -> None:
    response = client.post("/api/products", json={**PAYLOAD, "category": "vitamins"})
    assert response.status_code == 422


def test_blank_name_is_rejected(client: TestClient) -> None:
    assert client.post("/api/products", json={**PAYLOAD, "name": "   "}).status_code == 422


def test_missing_product_is_404(client: TestClient) -> None:
    response = client.get("/api/products/9999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Product 9999 was not found"


def test_delete_cascades_to_registrations(client: TestClient, registration: dict) -> None:
    product_id = registration["product_id"]
    assert client.delete(f"/api/products/{product_id}").status_code == 204
    assert client.get(f"/api/products/{product_id}").status_code == 404
    assert client.get(f"/api/registrations/{registration['id']}").status_code == 404
