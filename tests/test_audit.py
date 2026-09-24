"""Audit trail tests.

The trail exists to answer "who changed this, and what did it say before?"
months after the fact — so the tests lean on the cases where a naive
implementation quietly loses that answer: a record amended in a later request,
a cascade, and a deleted actor.
"""

from __future__ import annotations

import csv
import io

from fastapi.testclient import TestClient

from app.models import AuditEvent
from app.reference import Role
from tests.conftest import PASSWORD, days_from_now, make_user, sign_in


def events_for(client: TestClient, **params) -> list[dict]:
    response = client.get("/api/audit", params=params)
    assert response.status_code == 200, response.text
    return response.json()


class TestCapture:
    def test_creating_a_record_is_recorded(self, client: TestClient, product: dict) -> None:
        events = events_for(client, entity_type="product", entity_id=product["id"])
        assert len(events) == 1

        event = events[0]
        assert event["action"] == "create"
        assert event["actor_email"] == "editor@example.com"
        assert event["entity_label"] == "Imidacloprid 17.8% SL"
        assert event["changes"]["name"] == {"from": None, "to": "Imidacloprid 17.8% SL"}

    def test_update_records_both_sides_of_the_change(
        self, client: TestClient, registration: dict
    ) -> None:
        """The previous value is the point; a trail without it is half a trail."""
        new_expiry = days_from_now(500)
        response = client.patch(
            f"/api/registrations/{registration['id']}",
            json={"valid_until": new_expiry, "status": "under_renewal"},
        )
        assert response.status_code == 200

        event = events_for(client, entity_type="registration", action="update")[0]
        assert event["changes"]["valid_until"] == {
            "from": registration["valid_until"],
            "to": new_expiry,
        }
        assert event["changes"]["status"] == {"from": "active", "to": "under_renewal"}

    def test_unchanged_fields_are_not_recorded(
        self, client: TestClient, registration: dict
    ) -> None:
        client.patch(
            f"/api/registrations/{registration['id']}",
            json={"registrant_name": "Scimplify Agro Pvt Ltd"},  # same value
        )
        assert events_for(client, entity_type="registration", action="update") == []

    def test_delete_is_recorded_with_a_usable_label(
        self, client: TestClient, registration: dict
    ) -> None:
        assert client.delete(f"/api/registrations/{registration['id']}").status_code == 204

        event = events_for(client, entity_type="registration", action="delete")[0]
        assert event["entity_id"] == registration["id"]
        # The label has to stand alone: the record it names is gone.
        assert "CIR-123456/2023" in event["entity_label"]
        assert "Imidacloprid 17.8% SL" in event["entity_label"]

    def test_cascaded_deletes_are_each_recorded(
        self, client: TestClient, product: dict, registration: dict
    ) -> None:
        """Deleting a product takes compliance records with it; each must show."""
        permission = client.post(
            "/api/sale-permissions",
            json={
                "product_id": product["id"],
                "state": "Punjab",
                "permission_number": "PB/SP/2026/0001",
            },
        )
        assert permission.status_code == 201

        assert client.delete(f"/api/products/{product['id']}").status_code == 204

        deleted = {
            (event["entity_type"], event["entity_label"])
            for event in events_for(client, action="delete", limit=50)
        }
        types = {entity_type for entity_type, _ in deleted}
        assert types == {"product", "registration", "sale_permission"}, deleted

    def test_licence_product_links_are_recorded(
        self, client: TestClient, product: dict
    ) -> None:
        created = client.post(
            "/api/licences",
            json={
                "licence_number": "GJ/MFG/2026/019",
                "licence_type": "manufacture",
                "holder_name": "Scimplify Agro Pvt Ltd",
                "state": "Gujarat",
            },
        ).json()
        client.patch(f"/api/licences/{created['id']}", json={"product_ids": [product["id"]]})

        event = events_for(client, entity_type="licence", action="update")[0]
        assert event["changes"]["products"] == {"added": [product["id"]], "removed": []}

    def test_events_are_attributed_to_the_signed_in_account(
        self, client_factory, editor_id: int, admin_id: int
    ) -> None:
        editor = client_factory()
        sign_in(editor, "editor@example.com")
        admin = client_factory()
        sign_in(admin, "admin@example.com")

        editor.post(
            "/api/products",
            json={
                "name": "Editor's Product 1% EC",
                "active_ingredient": "A",
                "category": "insecticide",
                "formulation_type": "EC",
            },
        )
        admin.post(
            "/api/products",
            json={
                "name": "Admin's Product 2% EC",
                "active_ingredient": "B",
                "category": "herbicide",
                "formulation_type": "EC",
            },
        )

        by_label = {e["entity_label"]: e["actor_email"] for e in events_for(admin, entity_type="product")}
        assert by_label["Editor's Product 1% EC"] == "editor@example.com"
        assert by_label["Admin's Product 2% EC"] == "admin@example.com"


class TestSecretsAreNotRecorded:
    def test_password_values_never_reach_the_trail(
        self, admin_client: TestClient
    ) -> None:
        secret = "a-very-secret-passphrase"
        created = admin_client.post(
            "/api/users",
            json={
                "email": "recorded@example.com",
                "full_name": "Recorded Person",
                "password": secret,
            },
        )
        assert created.status_code == 201

        events = events_for(admin_client, entity_type="user")
        blob = str(events)
        assert secret not in blob
        assert "scrypt$" not in blob

        # On creation there is no previous value; what matters is that the
        # hash itself is never written down.
        created_event = next(e for e in events if e["action"] == "create")
        assert created_event["changes"]["password_hash"] == {
            "from": None,
            "to": "[redacted]",
        }

    def test_api_token_secret_never_reaches_the_trail(
        self, editor_client: TestClient, admin_client: TestClient
    ) -> None:
        token = editor_client.post("/api/tokens", json={"name": "ERP sync"}).json()["token"]
        blob = str(events_for(admin_client, entity_type="api_token"))
        assert token not in blob
        assert token.split("_", 1)[1][:12] not in blob

    def test_signing_in_does_not_fill_the_trail(
        self, client_factory, editor_id: int, admin_client: TestClient
    ) -> None:
        """last_login_at and friends are bookkeeping, not history."""
        before = len(events_for(admin_client, limit=500))
        for _ in range(3):
            sign_in(client_factory(), "editor@example.com")
        assert len(events_for(admin_client, limit=500)) == before


class TestDurability:
    def test_trail_outlives_the_account_that_made_it(
        self, admin_client: TestClient, client_factory, db_engine
    ) -> None:
        """A deleted user must not take their history with them."""
        user_id = make_user(db_engine, email="temp.staff@example.com", role=Role.EDITOR)
        staff = client_factory()
        sign_in(staff, "temp.staff@example.com")
        staff.post(
            "/api/products",
            json={
                "name": "Left Behind 5% SC",
                "active_ingredient": "C",
                "category": "fungicide",
                "formulation_type": "SC",
            },
        )

        assert admin_client.delete(f"/api/users/{user_id}").status_code == 204

        event = next(
            e
            for e in events_for(admin_client, entity_type="product", limit=50)
            if e["entity_label"] == "Left Behind 5% SC"
        )
        assert event["actor_email"] == "temp.staff@example.com"
        assert event["actor_name"] == "Temp.Staff"

    def test_changes_outside_a_request_are_attributed_to_the_system(
        self, session, admin_client: TestClient
    ) -> None:
        from app.models import Product
        from app.reference import FormulationType, ProductCategory

        session.add(
            Product(
                name="Shell Created 1% GR",
                active_ingredient="D",
                category=ProductCategory.HERBICIDE,
                formulation_type=FormulationType.GR,
            )
        )
        session.commit()

        event = next(
            e
            for e in events_for(admin_client, entity_type="product", limit=50)
            if e["entity_label"] == "Shell Created 1% GR"
        )
        assert event["actor_email"] == "system"

    def test_event_and_change_commit_together(
        self, client: TestClient, session, product: dict
    ) -> None:
        """A rejected change must leave no trace in the trail."""
        before = session.query(AuditEvent).count()
        rejected = client.post(
            "/api/products",
            json={
                "name": "Imidacloprid 17.8% SL",  # duplicate of the fixture
                "active_ingredient": "Imidacloprid",
                "category": "insecticide",
                "formulation_type": "SL",
            },
        )
        assert rejected.status_code == 409
        session.expire_all()
        assert session.query(AuditEvent).count() == before


class TestReadAccess:
    def test_anonymous_cannot_read_the_trail(self, raw_client: TestClient) -> None:
        assert raw_client.get("/api/audit").status_code == 401
        assert raw_client.get("/api/audit.csv").status_code == 401

    def test_viewer_may_read_the_register_trail(
        self, viewer_client: TestClient, product: dict
    ) -> None:
        events = events_for(viewer_client, entity_type="product")
        assert events and events[0]["entity_label"] == "Imidacloprid 17.8% SL"

    def test_viewer_may_not_read_the_account_trail(
        self, viewer_client: TestClient
    ) -> None:
        response = viewer_client.get("/api/audit", params={"entity_type": "user"})
        assert response.status_code == 403
        assert "admin role" in response.json()["detail"]

    def test_account_events_are_hidden_from_the_unfiltered_view(
        self, viewer_client: TestClient, admin_client: TestClient
    ) -> None:
        admin_client.post(
            "/api/users",
            json={
                "email": "hidden@example.com",
                "full_name": "Hidden",
                "password": "another-long-passphrase",
            },
        )
        seen = {e["entity_type"] for e in events_for(viewer_client, limit=500)}
        assert "user" not in seen

        admin_seen = {e["entity_type"] for e in events_for(admin_client, limit=500)}
        assert "user" in admin_seen

    def test_unknown_entity_type_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/audit", params={"entity_type": "spaceship"})
        assert response.status_code == 422

    def test_trail_is_append_only(self) -> None:
        """No route may amend or remove an event."""
        from app.main import app

        for route in app.routes:
            if getattr(route, "path", "").startswith("/api/audit"):
                assert set(getattr(route, "methods", set())) <= {"GET", "HEAD", "OPTIONS"}, route.path


class TestFiltersAndExport:
    def test_history_of_one_record(self, client: TestClient, registration: dict) -> None:
        client.patch(
            f"/api/registrations/{registration['id']}", json={"status": "under_renewal"}
        )
        client.patch(
            f"/api/registrations/{registration['id']}", json={"status": "suspended"}
        )

        history = events_for(
            client, entity_type="registration", entity_id=registration["id"]
        )
        assert [e["action"] for e in history] == ["update", "update", "create"]
        # Newest first.
        assert history[0]["changes"]["status"]["to"] == "suspended"
        assert history[1]["changes"]["status"]["to"] == "under_renewal"

    def test_filter_by_actor_and_action(self, client: TestClient, product: dict) -> None:
        assert events_for(client, actor_email="EDITOR@example.com", action="create")
        assert events_for(client, actor_email="nobody@example.com") == []

    def test_csv_export(self, client: TestClient, registration: dict) -> None:
        client.patch(
            f"/api/registrations/{registration['id']}", json={"status": "under_renewal"}
        )
        response = client.get("/api/audit.csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert rows
        update = next(r for r in rows if r["action"] == "update")
        assert update["actor_email"] == "editor@example.com"
        assert "status: 'active' -> 'under_renewal'" in update["changes"]
