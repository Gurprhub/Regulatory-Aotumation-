"""Authentication and authorisation tests.

The first class is the one that matters most: it asserts that nothing is
reachable without credentials. The rest cover roles, sessions, lockout and
API tokens.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import SESSION_COOKIE
from app.models import ApiToken, User, UserSession
from app.reference import Role
from tests.conftest import PASSWORD, make_user, sign_in

#: Every register endpoint, as (method, path, body).
READ_ENDPOINTS = [
    ("GET", "/api/products"),
    ("GET", "/api/registrations"),
    ("GET", "/api/sale-permissions"),
    ("GET", "/api/licences"),
    ("GET", "/api/label-approvals"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/alerts"),
    ("GET", "/api/alerts.csv"),
    ("GET", "/api/reference"),
]

WRITE_ENDPOINTS = [
    ("POST", "/api/products"),
    ("PATCH", "/api/products/1"),
    ("DELETE", "/api/products/1"),
    ("POST", "/api/registrations"),
    ("PATCH", "/api/registrations/1"),
    ("DELETE", "/api/registrations/1"),
    ("POST", "/api/sale-permissions"),
    ("PATCH", "/api/sale-permissions/1"),
    ("DELETE", "/api/sale-permissions/1"),
    ("POST", "/api/licences"),
    ("PATCH", "/api/licences/1"),
    ("DELETE", "/api/licences/1"),
    ("POST", "/api/label-approvals"),
    ("PATCH", "/api/label-approvals/1"),
    ("DELETE", "/api/label-approvals/1"),
]

ADMIN_ENDPOINTS = [
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PATCH", "/api/users/1"),
    ("DELETE", "/api/users/1"),
]


class TestNothingIsPublic:
    """No register endpoint may be reachable without credentials."""

    @pytest.mark.parametrize(("method", "path"), READ_ENDPOINTS + WRITE_ENDPOINTS + ADMIN_ENDPOINTS)
    def test_anonymous_is_rejected(self, raw_client: TestClient, method: str, path: str) -> None:
        response = raw_client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path} returned {response.status_code}"
        assert response.json()["detail"] == "Not authenticated"

    def test_every_api_route_requires_auth(self, raw_client: TestClient) -> None:
        """Guard against a future endpoint being added without protection."""
        from app.main import app

        # Sign-in must be reachable to sign in; sign-out is idempotent and
        # safe to call without a session, so it answers 204 either way.
        public = {"/api/auth/login", "/api/auth/logout"}
        missed = []
        for route in app.routes:
            path = getattr(route, "path", "")
            if not path.startswith("/api/") or path in public:
                continue
            for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
                concrete = path.replace("{token_id}", "1")
                for placeholder in ("{product_id}", "{registration_id}", "{permission_id}",
                                    "{licence_id}", "{approval_id}", "{user_id}"):
                    concrete = concrete.replace(placeholder, "1")
                response = raw_client.request(method, concrete, json={})
                if response.status_code != 401:
                    missed.append(f"{method} {path} -> {response.status_code}")
        assert not missed, f"reachable without authentication: {missed}"

    def test_unauthenticated_response_does_not_leak_data(self, raw_client: TestClient) -> None:
        body = raw_client.get("/api/dashboard").json()
        assert set(body) == {"detail"}

    def test_health_and_login_page_stay_public(self, raw_client: TestClient) -> None:
        """A liveness probe and the sign-in page must work before signing in."""
        assert raw_client.get("/health").status_code == 200
        assert raw_client.get("/login").status_code == 200
        assert raw_client.get("/").status_code == 200


class TestSignIn:
    def test_login_sets_a_session_cookie(self, raw_client: TestClient, editor_id: int) -> None:
        response = raw_client.post(
            "/api/auth/login", json={"email": "editor@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200
        assert response.json()["email"] == "editor@example.com"
        assert response.json()["role"] == "editor"
        assert SESSION_COOKIE in response.cookies

        cookie = response.cookies[SESSION_COOKIE]
        assert len(cookie) > 20  # a real secret, not a predictable value

    def test_login_is_case_insensitive_on_email(self, raw_client: TestClient, editor_id: int) -> None:
        response = raw_client.post(
            "/api/auth/login", json={"email": "  EDITOR@Example.COM ", "password": PASSWORD}
        )
        assert response.status_code == 200

    def test_password_is_not_stored_in_the_clear(self, db_engine, editor_id: int, session) -> None:
        user = session.get(User, editor_id)
        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("scrypt$")

    @pytest.mark.parametrize(
        ("email", "password"),
        [
            ("editor@example.com", "wrong-password-entirely"),
            ("nobody@example.com", PASSWORD),
        ],
    )
    def test_bad_credentials_are_indistinguishable(
        self, raw_client: TestClient, editor_id: int, email: str, password: str
    ) -> None:
        """A wrong password and an unknown address must look identical."""
        response = raw_client.post("/api/auth/login", json={"email": email, "password": password})
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email address or password"

    def test_deactivated_account_cannot_sign_in(self, raw_client: TestClient, db_engine) -> None:
        make_user(db_engine, email="gone@example.com", role=Role.EDITOR, is_active=False)
        response = raw_client.post(
            "/api/auth/login", json={"email": "gone@example.com", "password": PASSWORD}
        )
        assert response.status_code == 401

    def test_me_reports_the_signed_in_account(self, viewer_client: TestClient) -> None:
        body = viewer_client.get("/api/auth/me").json()
        assert body["email"] == "viewer@example.com"
        assert body["role"] == "viewer"
        assert "password_hash" not in body


class TestLockout:
    def test_account_locks_after_repeated_failures(
        self, raw_client: TestClient, editor_id: int, session
    ) -> None:
        from app.config import settings

        for _ in range(settings.max_failed_logins):
            raw_client.post(
                "/api/auth/login", json={"email": "editor@example.com", "password": "nope"}
            )

        # The correct password is now refused too, while the lock holds.
        response = raw_client.post(
            "/api/auth/login", json={"email": "editor@example.com", "password": PASSWORD}
        )
        assert response.status_code == 401

        session.expire_all()
        assert session.get(User, editor_id).locked_until is not None

    def test_lock_expires(self, raw_client: TestClient, editor_id: int, session) -> None:
        user = session.get(User, editor_id)
        user.locked_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()

        response = raw_client.post(
            "/api/auth/login", json={"email": "editor@example.com", "password": PASSWORD}
        )
        assert response.status_code == 200

    def test_successful_login_clears_the_failure_count(
        self, raw_client: TestClient, editor_id: int, session
    ) -> None:
        raw_client.post("/api/auth/login", json={"email": "editor@example.com", "password": "nope"})
        session.expire_all()
        assert session.get(User, editor_id).failed_login_count == 1

        sign_in(raw_client, "editor@example.com")
        session.expire_all()
        assert session.get(User, editor_id).failed_login_count == 0


class TestSessions:
    def test_logout_revokes_the_session_server_side(
        self, raw_client: TestClient, editor_id: int
    ) -> None:
        sign_in(raw_client, "editor@example.com")
        stolen = raw_client.cookies[SESSION_COOKIE]
        assert raw_client.get("/api/products").status_code == 200

        assert raw_client.post("/api/auth/logout").status_code == 204
        assert raw_client.get("/api/products").status_code == 401

        # A copy of the cookie taken before logout must be worthless.
        raw_client.cookies.set(SESSION_COOKIE, stolen)
        assert raw_client.get("/api/products").status_code == 401

    def test_expired_session_is_rejected(
        self, raw_client: TestClient, editor_id: int, session
    ) -> None:
        sign_in(raw_client, "editor@example.com")
        record = session.scalars(select(UserSession)).one()
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

        assert raw_client.get("/api/products").status_code == 401

    def test_forged_cookie_is_rejected(self, raw_client: TestClient, editor_id: int) -> None:
        raw_client.cookies.set(SESSION_COOKIE, "made-up-session-identifier")
        assert raw_client.get("/api/products").status_code == 401

    def test_changing_password_ends_existing_sessions(
        self, editor_id: int, client_factory
    ) -> None:
        """Every session goes, including ones held on other devices."""
        laptop = client_factory()
        phone = client_factory()
        sign_in(laptop, "editor@example.com")
        sign_in(phone, "editor@example.com")
        assert phone.get("/api/products").status_code == 200

        response = laptop.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-passphrase"},
        )
        assert response.status_code == 204

        # The session that made the change, and the one on the other device.
        assert laptop.get("/api/products").status_code == 401
        assert phone.get("/api/products").status_code == 401

    def test_new_password_works_after_a_change(self, editor_id: int, client_factory) -> None:
        changer = client_factory()
        sign_in(changer, "editor@example.com")
        assert changer.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "a-brand-new-passphrase"},
        ).status_code == 204

        fresh = client_factory()
        sign_in(fresh, "editor@example.com", "a-brand-new-passphrase")
        assert fresh.get("/api/products").status_code == 200

        stale = client_factory()
        response = stale.post(
            "/api/auth/login",
            json={"email": "editor@example.com", "password": PASSWORD},
        )
        assert response.status_code == 401, "the old password must stop working"

    def test_change_password_needs_the_current_one(self, editor_client: TestClient) -> None:
        response = editor_client.post(
            "/api/auth/change-password",
            json={"current_password": "not-it", "new_password": "another-long-passphrase"},
        )
        assert response.status_code == 403

    def test_short_new_password_is_rejected(self, editor_client: TestClient) -> None:
        response = editor_client.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "short"},
        )
        assert response.status_code == 422


class TestRoles:
    @pytest.mark.parametrize(("method", "path"), READ_ENDPOINTS)
    def test_viewer_may_read(self, viewer_client: TestClient, method: str, path: str) -> None:
        assert viewer_client.request(method, path).status_code == 200

    def test_viewer_may_not_write(self, viewer_client: TestClient) -> None:
        response = viewer_client.post(
            "/api/products",
            json={
                "name": "Sneaky 10% EC",
                "active_ingredient": "Something",
                "category": "insecticide",
                "formulation_type": "EC",
            },
        )
        assert response.status_code == 403
        assert "editor role" in response.json()["detail"]

    @pytest.mark.parametrize(("method", "path"), WRITE_ENDPOINTS)
    def test_viewer_is_refused_every_write(
        self, viewer_client: TestClient, method: str, path: str
    ) -> None:
        response = viewer_client.request(method, path, json={})
        assert response.status_code == 403, f"{method} {path} returned {response.status_code}"

    def test_editor_may_write_but_not_manage_users(self, editor_client: TestClient) -> None:
        assert editor_client.get("/api/products").status_code == 200
        response = editor_client.get("/api/users")
        assert response.status_code == 403
        assert "admin role" in response.json()["detail"]

    def test_admin_may_manage_users(self, admin_client: TestClient) -> None:
        created = admin_client.post(
            "/api/users",
            json={
                "email": "new.person@example.com",
                "full_name": "New Person",
                "role": "viewer",
                "password": "yet-another-long-passphrase",
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["role"] == "viewer"
        assert "password" not in created.json()

        listed = admin_client.get("/api/users").json()
        assert "new.person@example.com" in {user["email"] for user in listed}

    def test_duplicate_email_conflicts(self, admin_client: TestClient) -> None:
        payload = {
            "email": "dupe@example.com",
            "full_name": "Dupe",
            "password": "yet-another-long-passphrase",
        }
        assert admin_client.post("/api/users", json=payload).status_code == 201
        assert admin_client.post("/api/users", json=payload).status_code == 409

    def test_deactivating_a_user_ends_their_sessions(
        self, admin_client: TestClient, db_engine, client_factory
    ) -> None:
        user_id = make_user(db_engine, email="doomed@example.com", role=Role.EDITOR)

        victim = client_factory()
        victim.app = admin_client.app
        # Sign the victim in on their own client, sharing the same database.
        sign_in(victim, "doomed@example.com")
        assert victim.get("/api/products").status_code == 200

        assert admin_client.patch(f"/api/users/{user_id}", json={"is_active": False}).status_code == 200
        assert victim.get("/api/products").status_code == 401

    def test_last_admin_cannot_be_demoted(self, admin_client: TestClient, admin_id: int) -> None:
        response = admin_client.patch(f"/api/users/{admin_id}", json={"role": "viewer"})
        assert response.status_code == 409
        assert "only active administrator" in response.json()["detail"]

    def test_last_admin_cannot_be_deactivated(self, admin_client: TestClient, admin_id: int) -> None:
        response = admin_client.patch(f"/api/users/{admin_id}", json={"is_active": False})
        assert response.status_code == 409

    def test_admin_can_be_demoted_once_another_exists(
        self, admin_client: TestClient, admin_id: int, db_engine
    ) -> None:
        make_user(db_engine, email="second.admin@example.com", role=Role.ADMIN)
        response = admin_client.patch(f"/api/users/{admin_id}", json={"role": "viewer"})
        assert response.status_code == 200
        assert response.json()["role"] == "viewer"

    def test_admin_cannot_delete_themselves(self, admin_client: TestClient, admin_id: int) -> None:
        response = admin_client.delete(f"/api/users/{admin_id}")
        assert response.status_code == 409


class TestApiTokens:
    def _mint(self, client: TestClient, **kwargs) -> dict:
        response = client.post("/api/tokens", json={"name": "ERP sync", **kwargs})
        assert response.status_code == 201, response.text
        return response.json()

    def test_token_authenticates_a_request(self, editor_client: TestClient, client_factory) -> None:
        token = self._mint(editor_client)["token"]
        assert token.startswith("rat_")

        bare = client_factory()
        response = bare.get("/api/products", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200

    def test_plaintext_is_returned_once_and_never_stored(
        self, editor_client: TestClient, session
    ) -> None:
        token = self._mint(editor_client)["token"]

        stored = session.scalars(select(ApiToken)).one()
        assert stored.token_hash != token
        assert token not in stored.token_hash

        # Listing tokens must not hand the secret back.
        listed = editor_client.get("/api/tokens").json()
        assert "token" not in listed[0]

    def test_token_carries_the_owners_role(self, viewer_client: TestClient, client_factory) -> None:
        token = self._mint(viewer_client)["token"]
        bare = client_factory()
        headers = {"Authorization": f"Bearer {token}"}

        assert bare.get("/api/products", headers=headers).status_code == 200
        assert bare.post("/api/products", json={}, headers=headers).status_code == 403

    def test_revoked_token_stops_working(self, editor_client: TestClient, client_factory) -> None:
        created = self._mint(editor_client)
        headers = {"Authorization": f"Bearer {created['token']}"}
        bare = client_factory()
        assert bare.get("/api/products", headers=headers).status_code == 200

        assert editor_client.delete(f"/api/tokens/{created['id']}").status_code == 204
        assert bare.get("/api/products", headers=headers).status_code == 401

    def test_expired_token_is_rejected(self, editor_client: TestClient, session, client_factory) -> None:
        created = self._mint(editor_client, expires_in_days=30)
        record = session.get(ApiToken, created["id"])
        record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        session.commit()

        bare = client_factory()
        response = bare.get("/api/products", headers={"Authorization": f"Bearer {created['token']}"})
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "header",
        ["Bearer not-a-real-token", "Bearer rat_forged", "Basic abc", "rat_bare", ""],
    )
    def test_bad_authorization_headers_are_rejected(
        self, editor_client: TestClient, header: str, client_factory
    ) -> None:
        bare = client_factory()
        assert bare.get("/api/products", headers={"Authorization": header}).status_code == 401

    def test_token_dies_with_its_owners_account(
        self, editor_client: TestClient, admin_client: TestClient, db_engine, client_factory
    ) -> None:
        """Deactivating a user must stop their tokens, not just their sessions."""
        token = self._mint(editor_client)["token"]
        editor = next(
            u for u in admin_client.get("/api/users").json() if u["email"] == "editor@example.com"
        )
        assert admin_client.patch(f"/api/users/{editor['id']}", json={"is_active": False}).status_code == 200

        bare = client_factory()
        response = bare.get("/api/products", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    def test_users_only_see_their_own_tokens(
        self, editor_client: TestClient, db_engine, client_factory
    ) -> None:
        self._mint(editor_client)
        make_user(db_engine, email="other@example.com", role=Role.EDITOR)

        other = client_factory()
        sign_in(other, "other@example.com")
        assert other.get("/api/tokens").json() == []

    def test_non_admin_cannot_list_everyones_tokens(self, editor_client: TestClient) -> None:
        assert editor_client.get("/api/tokens", params={"all_users": True}).status_code == 403

    def test_cannot_revoke_someone_elses_token(
        self, editor_client: TestClient, db_engine, client_factory
    ) -> None:
        created = self._mint(editor_client)
        make_user(db_engine, email="nosy@example.com", role=Role.EDITOR)

        nosy = client_factory()
        sign_in(nosy, "nosy@example.com")
        # 404 rather than 403: the existence of someone else's token is not news.
        assert nosy.delete(f"/api/tokens/{created['id']}").status_code == 404
