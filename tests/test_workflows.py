"""
tests/test_workflows.py — Workflow CRUD: create, list, get, update, delete
"""
import pytest
import models
from conftest import make_user, make_group, make_workflow


class TestListWorkflows:
    def test_list_returns_all(self, client, db):
        admin = make_user(db, email="a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="s@x.com")
        group = make_group(db)
        make_workflow(db, admin, group, name="WF1")
        make_workflow(db, admin, group, name="WF2")
        db.commit()
        r = client.get(f"/api/workflows/?user_id={submitter.id}")
        assert r.status_code == 200
        names = [w["name"] for w in r.json()]
        assert "WF1" in names and "WF2" in names

    def test_list_unknown_user(self, client):
        r = client.get("/api/workflows/?user_id=99999")
        assert r.status_code == 404


class TestGetWorkflow:
    def test_get_existing(self, client, db):
        admin = make_user(db, email="ga@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.get(f"/api/workflows/{wf.id}?user_id={admin.id}")
        assert r.status_code == 200
        assert r.json()["id"] == wf.id

    def test_get_nonexistent(self, client, db):
        user = make_user(db, email="gn@x.com")
        db.commit()
        r = client.get(f"/api/workflows/99999?user_id={user.id}")
        assert r.status_code == 404

    def test_get_stages_included(self, client, db):
        admin = make_user(db, email="gs@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.get(f"/api/workflows/{wf.id}?user_id={admin.id}")
        data = r.json()
        assert "stages" in data
        assert len(data["stages"]) == 1


class TestCreateWorkflow:
    def _group_and_admin(self, db):
        admin = make_user(db, email="ca@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        return admin, group

    def test_create_minimal(self, client, db):
        admin, group = self._group_and_admin(db)
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "New WF", "type": "approval",
            "stages": [{
                "name": "Legal Review", "type": "approval",
                "order": 1, "approver_group_id": group.id
            }]
        })
        assert r.status_code == 200
        assert r.json()["name"] == "New WF"

    def test_create_with_all_fields(self, client, db):
        admin, group = self._group_and_admin(db)
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "Full WF", "type": "review",
            "description": "Full featured",
            "escalation_hours": 12,
            "rejection_behavior": "restart",
            "notification_channel": "both",
            "auto_approve_hours": 72,
            "amount_threshold": 5000.0,
            "stages": [{
                "name": "Review", "type": "review",
                "order": 1, "approver_group_id": group.id,
                "sla_hours": 24, "voting_rule": "all", "is_optional": False
            }]
        })
        assert r.status_code == 200
        data = r.json()
        assert data["rejection_behavior"] == "restart"
        assert data["amount_threshold"] == 5000.0

    def test_create_forbidden_for_submitter(self, client, db):
        submitter = make_user(db, email="sub@x.com", role=models.UserRole.submitter)
        group = make_group(db)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={submitter.id}", json={
            "name": "X", "type": "approval", "stages": []
        })
        assert r.status_code == 403

    def test_create_multi_stage(self, client, db):
        admin, group = self._group_and_admin(db)
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "Multi", "type": "approval",
            "stages": [
                {"name": "Stage A", "type": "approval", "order": 1, "approver_group_id": group.id},
                {"name": "Stage B", "type": "approval", "order": 2, "approver_group_id": group.id},
            ]
        })
        assert r.status_code == 200
        assert len(r.json()["stages"]) == 2

    def test_create_unknown_user(self, client):
        r = client.post("/api/workflows/?user_id=99999", json={
            "name": "X", "type": "approval", "stages": []
        })
        assert r.status_code == 404

    def test_create_missing_name(self, client, db):
        admin = make_user(db, email="mn@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "type": "approval", "stages": []
        })
        assert r.status_code == 422


class TestUpdateWorkflow:
    def test_update_name(self, client, db):
        admin = make_user(db, email="ua@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.patch(f"/api/workflows/{wf.id}?user_id={admin.id}", json={
            "name": "Renamed WF"
        })
        assert r.status_code == 200
        assert r.json()["name"] == "Renamed WF"

    def test_update_deactivate(self, client, db):
        admin = make_user(db, email="ud@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.patch(f"/api/workflows/{wf.id}?user_id={admin.id}", json={
            "is_active": False
        })
        assert r.status_code == 200
        assert r.json()["is_active"] is False

    def test_update_replace_stages(self, client, db):
        admin = make_user(db, email="urs@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.patch(f"/api/workflows/{wf.id}?user_id={admin.id}", json={
            "stages": [
                {"name": "New Stage", "type": "approval", "order": 1, "approver_group_id": group.id}
            ]
        })
        assert r.status_code == 200
        stages = r.json()["stages"]
        assert len(stages) == 1
        assert stages[0]["name"] == "New Stage"

    def test_update_nonexistent(self, client, db):
        admin = make_user(db, email="une@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.patch(f"/api/workflows/99999?user_id={admin.id}", json={"name": "X"})
        assert r.status_code == 404

    def test_update_forbidden_for_non_admin(self, client, db):
        approver = make_user(db, email="ufa@x.com", role=models.UserRole.approver)
        admin = make_user(db, email="ufadm@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.patch(f"/api/workflows/{wf.id}?user_id={approver.id}", json={"name": "X"})
        assert r.status_code == 403


class TestDeleteWorkflow:
    def test_delete_success(self, client, db):
        admin = make_user(db, email="da@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.delete(f"/api/workflows/{wf.id}?user_id={admin.id}")
        assert r.status_code == 200
        assert "Deleted" in r.json()["detail"]

    def test_delete_nonexistent(self, client, db):
        admin = make_user(db, email="dne@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.delete(f"/api/workflows/99999?user_id={admin.id}")
        assert r.status_code == 404

    def test_delete_forbidden_for_submitter(self, client, db):
        submitter = make_user(db, email="dsub@x.com")
        admin = make_user(db, email="dadm@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.delete(f"/api/workflows/{wf.id}?user_id={submitter.id}")
        assert r.status_code == 403
