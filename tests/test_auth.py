"""
tests/test_auth.py — Auth router: register, login, refresh, /me
"""
import pytest
from conftest import make_user


# ── /api/auth/register ────────────────────────────────────────────────────────

class TestRegister:
    def test_register_success(self, client):
        r = client.post("/api/auth/register", json={
            "firstName": "Alice", "lastName": "Smith",
            "email": "alice@example.com", "password": "secret123"
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "alice@example.com"
        assert "id" in data
        assert "password" not in data  # password must never be exposed

    def test_register_duplicate_email(self, client, db):
        make_user(db, email="dup@example.com")
        db.commit()
        r = client.post("/api/auth/register", json={
            "firstName": "Bob", "email": "dup@example.com", "password": "pass"
        })
        assert r.status_code == 400
        assert "already registered" in r.json()["detail"]

    def test_register_missing_required_fields(self, client):
        r = client.post("/api/auth/register", json={"email": "x@x.com"})
        assert r.status_code == 422

    def test_register_invalid_email(self, client):
        r = client.post("/api/auth/register", json={
            "firstName": "X", "email": "not-an-email", "password": "pass"
        })
        assert r.status_code == 422

    def test_register_with_role(self, client):
        r = client.post("/api/auth/register", json={
            "firstName": "Carol", "email": "carol@example.com",
            "password": "pass", "role": "approver"
        })
        assert r.status_code == 200
        assert r.json()["role"] == "approver"


# ── /api/auth/login ───────────────────────────────────────────────────────────

class TestLogin:
    def test_login_success(self, client, db):
        make_user(db, email="login@example.com", password="mypassword")
        db.commit()
        r = client.post("/api/auth/login", json={
            "email": "login@example.com", "password": "mypassword"
        })
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "login@example.com"

    def test_login_wrong_password(self, client, db):
        make_user(db, email="pw@example.com", password="correct")
        db.commit()
        r = client.post("/api/auth/login", json={
            "email": "pw@example.com", "password": "wrong"
        })
        assert r.status_code == 401

    def test_login_unknown_email(self, client):
        r = client.post("/api/auth/login", json={
            "email": "ghost@example.com", "password": "any"
        })
        assert r.status_code == 401

    def test_login_inactive_user(self, client, db):
        make_user(db, email="inactive@example.com", password="pass", is_active=False)
        db.commit()
        r = client.post("/api/auth/login", json={
            "email": "inactive@example.com", "password": "pass"
        })
        assert r.status_code in (400, 401)

    def test_login_missing_fields(self, client):
        r = client.post("/api/auth/login", json={"email": "x@x.com"})
        assert r.status_code == 422


# ── /api/auth/refresh ─────────────────────────────────────────────────────────

class TestRefresh:
    def _get_refresh_token(self, client, db):
        """Helper: register + login and extract refresh token (via auth_utils directly)."""
        from auth_utils import create_refresh_token
        user = make_user(db, email="refresh@example.com")
        db.commit()
        return create_refresh_token(user.id), user

    def test_refresh_success(self, client, db):
        from auth_utils import create_refresh_token
        user = make_user(db, email="rfr@example.com")
        db.commit()
        token = create_refresh_token(user.id)
        r = client.post("/api/auth/refresh", json={"refresh_token": token})
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_refresh_invalid_token(self, client):
        r = client.post("/api/auth/refresh", json={"refresh_token": "garbage.token.here"})
        assert r.status_code == 401

    def test_refresh_access_token_rejected(self, client, db):
        """Access tokens must not be accepted as refresh tokens."""
        from auth_utils import create_access_token
        user = make_user(db, email="rfr2@example.com")
        db.commit()
        token = create_access_token(data={"sub": user.id})
        r = client.post("/api/auth/refresh", json={"refresh_token": token})
        assert r.status_code == 401

    def test_refresh_inactive_user(self, client, db):
        from auth_utils import create_refresh_token
        user = make_user(db, email="rfr3@example.com", is_active=False)
        db.commit()
        token = create_refresh_token(user.id)
        r = client.post("/api/auth/refresh", json={"refresh_token": token})
        assert r.status_code == 401


# ── /api/auth/me ──────────────────────────────────────────────────────────────

class TestMe:
    def test_me_success(self, client, db):
        from auth_utils import create_access_token
        user = make_user(db, email="me@example.com")
        db.commit()
        token = create_access_token(data={"sub": user.id})
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["email"] == "me@example.com"

    def test_me_no_token(self, client):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_me_bad_token(self, client):
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer bad.token"})
        assert r.status_code == 401


# ── /api/auth/me/out-of-office ────────────────────────────────────────────────

class TestOutOfOffice:
    """PATCH /api/auth/me/out-of-office — self-service OOO + delegate."""

    def _token(self, db, email):
        from auth_utils import create_access_token
        user = make_user(db, email=email)
        db.commit()
        return create_access_token(data={"sub": user.id}), user

    def test_set_ooo_with_delegate(self, client, db):
        from datetime import datetime, timedelta
        token, user = self._token(db, "ooo1@x.com")
        delegate = make_user(db, email="ooo1d@x.com")
        db.commit()
        future = (datetime.utcnow() + timedelta(days=3)).isoformat()
        r = client.patch(
            "/api/auth/me/out-of-office",
            json={"ooo_until": future, "delegate_id": delegate.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ooo_until"] is not None
        assert data["delegate_id"] == delegate.id

    def test_clear_ooo(self, client, db):
        from datetime import datetime, timedelta
        token, user = self._token(db, "ooo2@x.com")
        delegate = make_user(db, email="ooo2d@x.com")
        user.ooo_until = datetime.utcnow() + timedelta(days=2)
        user.delegate_id = delegate.id
        db.commit()
        r = client.patch(
            "/api/auth/me/out-of-office",
            json={"ooo_until": None, "delegate_id": None},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ooo_until"] is None
        assert data["delegate_id"] is None

    def test_cannot_delegate_to_self(self, client, db):
        from datetime import datetime, timedelta
        token, user = self._token(db, "ooo3@x.com")
        future = (datetime.utcnow() + timedelta(days=1)).isoformat()
        r = client.patch(
            "/api/auth/me/out-of-office",
            json={"ooo_until": future, "delegate_id": user.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    def test_nonexistent_delegate_rejected(self, client, db):
        from datetime import datetime, timedelta
        token, user = self._token(db, "ooo4@x.com")
        future = (datetime.utcnow() + timedelta(days=1)).isoformat()
        r = client.patch(
            "/api/auth/me/out-of-office",
            json={"ooo_until": future, "delegate_id": 99999},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    def test_no_token_unauthorized(self, client):
        r = client.patch(
            "/api/auth/me/out-of-office",
            json={"ooo_until": None, "delegate_id": None},
        )
        assert r.status_code == 401

    def test_set_ooo_without_delegate(self, client, db):
        """ooo_until can be set without a delegate — notifications still go
        to the principal but no one can stand in for them."""
        from datetime import datetime, timedelta
        token, user = self._token(db, "ooo5@x.com")
        future = (datetime.utcnow() + timedelta(days=1)).isoformat()
        r = client.patch(
            "/api/auth/me/out-of-office",
            json={"ooo_until": future, "delegate_id": None},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["delegate_id"] is None
        assert r.json()["ooo_until"] is not None

    def test_ooo_propagates_to_live_stand_in_flow(self, client, db):
        """End-to-end: set OOO via the endpoint, then verify the delegate
        can immediately act as stand-in on an active approval stage."""
        import models
        from auth_utils import create_access_token
        from datetime import datetime, timedelta
        from conftest import make_group, make_workflow, make_request

        admin = make_user(db, email="oooe2e-admin@x.com", role=models.UserRole.admin)
        principal = make_user(db, email="oooe2e-p@x.com", role=models.UserRole.approver)
        delegate = make_user(db, email="oooe2e-d@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="oooe2e-s@x.com")
        group = make_group(db, members=[principal])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()

        principal_token = create_access_token(data={"sub": principal.id})
        future = (datetime.utcnow() + timedelta(days=2)).isoformat()
        r_ooo = client.patch(
            "/api/auth/me/out-of-office",
            json={"ooo_until": future, "delegate_id": delegate.id},
            headers={"Authorization": f"Bearer {principal_token}"},
        )
        assert r_ooo.status_code == 200

        r_act = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r_act.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved
