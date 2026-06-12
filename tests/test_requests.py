"""
tests/test_requests.py — Request submission, listing, retrieval, cancel, auto-approve
"""
import pytest
import models
from conftest import make_user, make_group, make_workflow, make_request


class TestSubmitRequest:
    def test_submit_success(self, client, db):
        admin = make_user(db, email="sra@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="srs@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "New Invoice", "workflow_id": wf.id
        })
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "New Invoice"
        assert data["status"] == "pending"
        assert data["current_stage"] == 1

    def test_submit_creates_request_stages(self, client, db):
        admin = make_user(db, email="scs@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="scss@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Doc A", "workflow_id": wf.id
        })
        assert r.status_code == 201
        assert len(r.json()["stages"]) == 1

    def test_submit_inactive_workflow(self, client, db):
        admin = make_user(db, email="siw@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="siws@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        wf.is_active = False
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Inactive WF Test", "workflow_id": wf.id
        })
        assert r.status_code == 404

    def test_submit_nonexistent_workflow(self, client, db):
        submitter = make_user(db, email="snw@x.com")
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Ghost WF", "workflow_id": 99999
        })
        assert r.status_code == 404

    def test_submit_unknown_user(self, client):
        r = client.post("/api/requests/?user_id=99999", json={
            "title": "X", "workflow_id": 1
        })
        assert r.status_code == 404

    def test_submit_missing_title(self, client, db):
        submitter = make_user(db, email="smt@x.com")
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "workflow_id": 1
        })
        assert r.status_code == 422

    def test_submit_with_all_optional_fields(self, client, db):
        admin = make_user(db, email="sao@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="saos@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Full Request",
            "description": "Detailed description",
            "document_name": "invoice.pdf",
            "document_url": "/uploads/invoice.pdf",
            "document_type": "pdf",
            "amount": 1500.0,
            "department": "Engineering",
            "request_type": "vendor",
            "workflow_id": wf.id
        })
        assert r.status_code == 201
        data = r.json()
        assert data["amount"] == 1500.0
        assert data["department"] == "Engineering"


class TestAutoApprove:
    def test_auto_approve_below_threshold(self, client, db):
        admin = make_user(db, email="aat@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="aats@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group, amount_threshold=1000.0)
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Small Invoice", "workflow_id": wf.id, "amount": 500.0
        })
        assert r.status_code == 201
        assert r.json()["status"] == "approved"

    def test_no_auto_approve_above_threshold(self, client, db):
        admin = make_user(db, email="naat@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="naats@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group, amount_threshold=1000.0)
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Large Invoice", "workflow_id": wf.id, "amount": 5000.0
        })
        assert r.status_code == 201
        assert r.json()["status"] == "pending"

    def test_auto_approve_at_exact_threshold(self, client, db):
        """Amount exactly equal to threshold should auto-approve (<= check)."""
        admin = make_user(db, email="aaet@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="aaets@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group, amount_threshold=1000.0)
        db.commit()
        r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Exact Threshold", "workflow_id": wf.id, "amount": 1000.0
        })
        assert r.status_code == 201
        assert r.json()["status"] == "approved"


class TestListRequests:
    def test_admin_sees_all(self, client, db):
        admin = make_user(db, email="lra@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="lrs@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        make_request(db, submitter, wf, title="Req A")
        make_request(db, submitter, wf, title="Req B")
        db.commit()
        r = client.get(f"/api/requests/?user_id={admin.id}")
        assert r.status_code == 200
        titles = [req["title"] for req in r.json()]
        assert "Req A" in titles and "Req B" in titles

    def test_submitter_sees_only_own(self, client, db):
        admin = make_user(db, email="lrso@x.com", role=models.UserRole.admin)
        s1 = make_user(db, email="lrss1@x.com")
        s2 = make_user(db, email="lrss2@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        make_request(db, s1, wf, title="S1 Request")
        make_request(db, s2, wf, title="S2 Request")
        db.commit()
        r = client.get(f"/api/requests/?user_id={s1.id}")
        assert r.status_code == 200
        titles = [req["title"] for req in r.json()]
        assert "S1 Request" in titles
        assert "S2 Request" not in titles

    def test_filter_by_status(self, client, db):
        admin = make_user(db, email="lrfs@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="lrfss@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf, title="Pending One")
        make_request(db, submitter, wf, title="Pending Two")
        # Mark one as approved
        req.status = models.RequestStatus.approved
        db.commit()
        r = client.get(f"/api/requests/?user_id={admin.id}&status=approved")
        assert r.status_code == 200
        for item in r.json():
            assert item["status"] == "approved"

    def test_filter_by_workflow(self, client, db):
        admin = make_user(db, email="lrfw@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="lrfws@x.com")
        group = make_group(db)
        wf1 = make_workflow(db, admin, group, name="WF Alpha")
        wf2 = make_workflow(db, admin, group, name="WF Beta")
        make_request(db, submitter, wf1, title="Alpha Request")
        make_request(db, submitter, wf2, title="Beta Request")
        db.commit()
        r = client.get(f"/api/requests/?user_id={admin.id}&workflow_id={wf1.id}")
        assert r.status_code == 200
        for item in r.json():
            assert item["workflow_id"] == wf1.id


class TestGetRequest:
    def test_get_own_request(self, client, db):
        admin = make_user(db, email="gor@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="gors@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        r = client.get(f"/api/requests/{req.id}?user_id={submitter.id}")
        assert r.status_code == 200
        assert r.json()["id"] == req.id

    def test_get_others_request_as_non_approver(self, client, db):
        admin = make_user(db, email="goa@x.com", role=models.UserRole.admin)
        s1 = make_user(db, email="goas1@x.com")
        s2 = make_user(db, email="goas2@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, s1, wf)
        db.commit()
        r = client.get(f"/api/requests/{req.id}?user_id={s2.id}")
        assert r.status_code == 403

    def test_get_nonexistent_request(self, client, db):
        user = make_user(db, email="gnr@x.com")
        db.commit()
        r = client.get(f"/api/requests/99999?user_id={user.id}")
        assert r.status_code == 404

    def test_admin_can_get_any_request(self, client, db):
        admin = make_user(db, email="aga@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="agas@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        r = client.get(f"/api/requests/{req.id}?user_id={admin.id}")
        assert r.status_code == 200

    def test_history_and_stages_in_response(self, client, db):
        admin = make_user(db, email="has@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="hass@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        r = client.get(f"/api/requests/{req.id}?user_id={admin.id}")
        data = r.json()
        assert "stages" in data
        assert "history" in data


class TestCancelRequest:
    def test_cancel_own_pending_request(self, client, db):
        admin = make_user(db, email="cop@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="cops@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        r = client.patch(f"/api/requests/{req.id}/cancel?user_id={submitter.id}")
        assert r.status_code == 200

    def test_cancel_already_approved(self, client, db):
        admin = make_user(db, email="caa@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="caas@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        req.status = models.RequestStatus.approved
        db.commit()
        r = client.patch(f"/api/requests/{req.id}/cancel?user_id={submitter.id}")
        assert r.status_code == 400

    def test_cancel_others_request_forbidden(self, client, db):
        admin = make_user(db, email="cof@x.com", role=models.UserRole.admin)
        s1 = make_user(db, email="cofs1@x.com")
        s2 = make_user(db, email="cofs2@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, s1, wf)
        db.commit()
        r = client.patch(f"/api/requests/{req.id}/cancel?user_id={s2.id}")
        assert r.status_code == 403

    def test_admin_can_cancel_any(self, client, db):
        admin = make_user(db, email="aca@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="acas@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        r = client.patch(f"/api/requests/{req.id}/cancel?user_id={admin.id}")
        assert r.status_code == 200

    def test_cancel_nonexistent(self, client, db):
        user = make_user(db, email="cne@x.com")
        db.commit()
        r = client.patch(f"/api/requests/99999/cancel?user_id={user.id}")
        assert r.status_code == 404
