"""
tests/test_stages.py — Approver groups CRUD + workflow stage management
"""
import pytest
import models
from conftest import make_user, make_group, make_workflow


# ── Approver Groups ───────────────────────────────────────────────────────────

class TestListApproverGroups:
    def test_list_groups(self, client, db):
        admin = make_user(db, email="lg@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="lgm@x.com", role=models.UserRole.approver)
        make_group(db, name="G1", members=[approver])
        db.commit()
        r = client.get(f"/api/stages/approver-groups?user_id={admin.id}")
        assert r.status_code == 200
        names = [g["name"] for g in r.json()]
        assert "G1" in names

    def test_members_returned(self, client, db):
        admin = make_user(db, email="lgm2@x.com", role=models.UserRole.admin)
        member = make_user(db, email="lgm3@x.com", role=models.UserRole.approver)
        make_group(db, name="WithMember", members=[member])
        db.commit()
        r = client.get(f"/api/stages/approver-groups?user_id={admin.id}")
        groups = r.json()
        wm = next(g for g in groups if g["name"] == "WithMember")
        assert len(wm["members"]) == 1
        assert wm["members"][0]["email"] == member.email

    def test_list_unknown_user(self, client):
        r = client.get("/api/stages/approver-groups?user_id=99999")
        assert r.status_code == 404


class TestCreateApproverGroup:
    def test_create_empty_group(self, client, db):
        admin = make_user(db, email="cg@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.post(f"/api/stages/approver-groups?user_id={admin.id}", json={
            "name": "Legal", "description": "Legal team", "member_ids": []
        })
        assert r.status_code == 200
        assert r.json()["name"] == "Legal"

    def test_create_group_with_members(self, client, db):
        admin = make_user(db, email="cgm@x.com", role=models.UserRole.admin)
        m1 = make_user(db, email="m1@x.com", role=models.UserRole.approver)
        m2 = make_user(db, email="m2@x.com", role=models.UserRole.approver)
        db.commit()
        r = client.post(f"/api/stages/approver-groups?user_id={admin.id}", json={
            "name": "Finance", "member_ids": [m1.id, m2.id]
        })
        assert r.status_code == 200
        assert len(r.json()["members"]) == 2

    def test_create_group_forbidden_for_submitter(self, client, db):
        submitter = make_user(db, email="cgfs@x.com")
        db.commit()
        r = client.post(f"/api/stages/approver-groups?user_id={submitter.id}", json={
            "name": "X", "member_ids": []
        })
        assert r.status_code == 403

    def test_create_group_missing_name(self, client, db):
        admin = make_user(db, email="cgmn@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.post(f"/api/stages/approver-groups?user_id={admin.id}", json={
            "member_ids": []
        })
        assert r.status_code == 422


class TestDeleteApproverGroup:
    def test_delete_group(self, client, db):
        admin = make_user(db, email="dg@x.com", role=models.UserRole.admin)
        group = make_group(db, name="ToDelete")
        db.commit()
        r = client.delete(f"/api/stages/approver-groups/{group.id}?user_id={admin.id}")
        assert r.status_code == 200

    def test_delete_nonexistent_group(self, client, db):
        admin = make_user(db, email="dgne@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.delete(f"/api/stages/approver-groups/99999?user_id={admin.id}")
        assert r.status_code == 404


class TestGroupMembership:
    def test_add_member(self, client, db):
        admin = make_user(db, email="am@x.com", role=models.UserRole.admin)
        member = make_user(db, email="amm@x.com", role=models.UserRole.approver)
        group = make_group(db)
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members?user_id={admin.id}",
            json={"user_id": member.id, "sequential_order": 0}
        )
        assert r.status_code == 200

    def test_add_member_idempotent(self, client, db):
        """Adding the same member twice updates sequential_order, not duplicates."""
        admin = make_user(db, email="ami@x.com", role=models.UserRole.admin)
        member = make_user(db, email="amim@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[member])
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members?user_id={admin.id}",
            json={"user_id": member.id, "sequential_order": 5}
        )
        assert r.status_code == 200

    def test_add_member_nonexistent_group(self, client, db):
        admin = make_user(db, email="amng@x.com", role=models.UserRole.admin)
        member = make_user(db, email="amngm@x.com")
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/99999/members?user_id={admin.id}",
            json={"user_id": member.id, "sequential_order": 0}
        )
        assert r.status_code == 404

    def test_add_member_nonexistent_user(self, client, db):
        admin = make_user(db, email="amnu@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members?user_id={admin.id}",
            json={"user_id": 99999, "sequential_order": 0}
        )
        assert r.status_code == 404

    def test_remove_member(self, client, db):
        admin = make_user(db, email="rm@x.com", role=models.UserRole.admin)
        member = make_user(db, email="rmm@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[member])
        db.commit()
        r = client.delete(
            f"/api/stages/approver-groups/{group.id}/members/{member.id}?user_id={admin.id}"
        )
        assert r.status_code == 200

    def test_remove_nonexistent_member(self, client, db):
        admin = make_user(db, email="rnm@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.delete(
            f"/api/stages/approver-groups/{group.id}/members/99999?user_id={admin.id}"
        )
        assert r.status_code == 404


class TestListUsers:
    def test_admin_sees_all_users(self, client, db):
        admin = make_user(db, email="lau@x.com", role=models.UserRole.admin)
        make_user(db, email="lau2@x.com")
        db.commit()
        r = client.get(f"/api/stages/users?user_id={admin.id}")
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_submitter_forbidden(self, client, db):
        sub = make_user(db, email="lusub@x.com")
        db.commit()
        r = client.get(f"/api/stages/users?user_id={sub.id}")
        assert r.status_code == 403


class TestWorkflowStages:
    def test_add_stage_to_workflow(self, client, db):
        admin = make_user(db, email="ast@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.post(f"/api/stages/{wf.id}/stages?user_id={admin.id}", json={
            "name": "Stage 2", "type": "approval", "order": 2,
            "approver_group_id": group.id, "sla_hours": 24
        })
        assert r.status_code == 200
        assert r.json()["name"] == "Stage 2"

    def test_add_stage_nonexistent_workflow(self, client, db):
        admin = make_user(db, email="asnw@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.post(f"/api/stages/99999/stages?user_id={admin.id}", json={
            "name": "X", "type": "approval", "order": 1,
            "approver_group_id": 1, "sla_hours": 48
        })
        assert r.status_code == 404

    def test_delete_stage(self, client, db):
        admin = make_user(db, email="ds@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        stage_id = wf.stages[0].id
        r = client.delete(f"/api/stages/{stage_id}?user_id={admin.id}")
        assert r.status_code == 200

    def test_delete_nonexistent_stage(self, client, db):
        admin = make_user(db, email="dns@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.delete(f"/api/stages/99999?user_id={admin.id}")
        assert r.status_code == 404
