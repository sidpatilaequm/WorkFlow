"""
tests/test_stages.py — Approver groups CRUD + workflow stage management
"""
import pytest
import models
from conftest import make_user, make_group, make_workflow
from auth_utils import create_access_token


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


class TestSetMemberOptional:
    def test_toggle_optional_true(self, client, db):
        admin = make_user(db, email="smo@x.com", role=models.UserRole.admin)
        member = make_user(db, email="smom@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[member])
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.patch(
            f"/api/stages/approver-groups/{group.id}/members/{member.id}",
            json={"is_optional": True, "sequential_order": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["is_optional"] is True

    def test_toggle_optional_false(self, client, db):
        admin = make_user(db, email="smof@x.com", role=models.UserRole.admin)
        member = make_user(db, email="smofm@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[member])
        gm = db.query(models.ApproverGroupMember).filter(
            models.ApproverGroupMember.group_id == group.id,
            models.ApproverGroupMember.user_id == member.id,
        ).first()
        gm.is_optional = True
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.patch(
            f"/api/stages/approver-groups/{group.id}/members/{member.id}",
            json={"is_optional": False, "sequential_order": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["is_optional"] is False

    def test_toggle_nonexistent_member(self, client, db):
        admin = make_user(db, email="smne@x.com", role=models.UserRole.admin)
        group = make_group(db)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.patch(
            f"/api/stages/approver-groups/{group.id}/members/99999",
            json={"is_optional": True, "sequential_order": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    def test_toggle_forbidden_for_non_admin(self, client, db):
        approver = make_user(db, email="smnf@x.com", role=models.UserRole.approver)
        member = make_user(db, email="smnfm@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[member])
        token = create_access_token(data={"sub": approver.id})
        db.commit()
        r = client.patch(
            f"/api/stages/approver-groups/{group.id}/members/{member.id}",
            json={"is_optional": True, "sequential_order": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_toggle_no_token(self, client, db):
        member = make_user(db, email="smnt@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[member])
        db.commit()
        r = client.patch(
            f"/api/stages/approver-groups/{group.id}/members/{member.id}",
            json={"is_optional": True, "sequential_order": 0},
        )
        assert r.status_code == 401


class TestSubstituteMember:
    def test_substitute_swaps_user(self, client, db):
        admin = make_user(db, email="sub1@x.com", role=models.UserRole.admin)
        old_member = make_user(db, email="sub1old@x.com", role=models.UserRole.approver)
        new_member = make_user(db, email="sub1new@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[old_member])
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{old_member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": new_member.id},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["replaced_user_id"] == old_member.id
        assert data["new_user_id"] == new_member.id

        # The membership row itself now points at the new user, old user is gone.
        remaining = db.query(models.ApproverGroupMember).filter(
            models.ApproverGroupMember.group_id == group.id
        ).all()
        assert len(remaining) == 1
        assert remaining[0].user_id == new_member.id

    def test_substitute_preserves_sequential_order_and_optional(self, client, db):
        admin = make_user(db, email="sub2@x.com", role=models.UserRole.admin)
        old_member = make_user(db, email="sub2old@x.com", role=models.UserRole.approver)
        new_member = make_user(db, email="sub2new@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[old_member])
        gm = db.query(models.ApproverGroupMember).filter(
            models.ApproverGroupMember.group_id == group.id,
            models.ApproverGroupMember.user_id == old_member.id,
        ).first()
        gm.is_optional = True
        gm.sequential_order = 3
        db.commit()

        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{old_member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": new_member.id},
        )
        assert r.status_code == 200

        gm2 = db.query(models.ApproverGroupMember).filter(
            models.ApproverGroupMember.group_id == group.id,
            models.ApproverGroupMember.user_id == new_member.id,
        ).first()
        assert gm2 is not None
        assert gm2.is_optional is True
        assert gm2.sequential_order == 3

    def test_substitute_nonexistent_group(self, client, db):
        admin = make_user(db, email="sub3@x.com", role=models.UserRole.admin)
        new_member = make_user(db, email="sub3new@x.com", role=models.UserRole.approver)
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/99999/members/1/substitute?user_id={admin.id}",
            json={"new_user_id": new_member.id},
        )
        assert r.status_code == 404

    def test_substitute_member_not_in_group(self, client, db):
        admin = make_user(db, email="sub4@x.com", role=models.UserRole.admin)
        not_a_member = make_user(db, email="sub4nm@x.com", role=models.UserRole.approver)
        new_member = make_user(db, email="sub4new@x.com", role=models.UserRole.approver)
        group = make_group(db)
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{not_a_member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": new_member.id},
        )
        assert r.status_code == 404

    def test_substitute_nonexistent_new_user(self, client, db):
        admin = make_user(db, email="sub5@x.com", role=models.UserRole.admin)
        old_member = make_user(db, email="sub5old@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[old_member])
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{old_member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": 99999},
        )
        assert r.status_code == 404

    def test_substitute_same_user_rejected(self, client, db):
        admin = make_user(db, email="sub6@x.com", role=models.UserRole.admin)
        member = make_user(db, email="sub6m@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[member])
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": member.id},
        )
        assert r.status_code == 400

    def test_substitute_new_user_already_in_group(self, client, db):
        admin = make_user(db, email="sub7@x.com", role=models.UserRole.admin)
        old_member = make_user(db, email="sub7old@x.com", role=models.UserRole.approver)
        already_in = make_user(db, email="sub7ai@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[old_member, already_in])
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{old_member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": already_in.id},
        )
        assert r.status_code == 400

    def test_substitute_forbidden_for_non_admin(self, client, db):
        approver = make_user(db, email="sub8@x.com", role=models.UserRole.approver)
        old_member = make_user(db, email="sub8old@x.com", role=models.UserRole.approver)
        new_member = make_user(db, email="sub8new@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[old_member])
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{old_member.id}/substitute"
            f"?user_id={approver.id}",
            json={"new_user_id": new_member.id},
        )
        assert r.status_code == 403

    def test_substitute_inactive_new_user(self, client, db):
        admin = make_user(db, email="sub9@x.com", role=models.UserRole.admin)
        old_member = make_user(db, email="sub9old@x.com", role=models.UserRole.approver)
        inactive_user = make_user(db, email="sub9inactive@x.com", role=models.UserRole.approver, is_active=False)
        group = make_group(db, members=[old_member])
        db.commit()
        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{old_member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": inactive_user.id},
        )
        assert r.status_code == 404

    def test_substitute_does_not_affect_in_flight_request_snapshot(self, client, db):
        """
        A substitution on the live group must not retroactively change who
        the approver is on a request already submitted under the old group
        membership — that's frozen in workflow_snapshot at submission time.
        """
        admin = make_user(db, email="sub10@x.com", role=models.UserRole.admin)
        old_member = make_user(db, email="sub10old@x.com", role=models.UserRole.approver)
        new_member = make_user(db, email="sub10new@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="sub10sub@x.com")
        group = make_group(db, members=[old_member])
        wf = make_workflow(db, admin, group)
        db.commit()

        sub_r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Pre-substitution request", "workflow_id": wf.id
        })
        assert sub_r.status_code == 201
        req_id = sub_r.json()["id"]

        r = client.post(
            f"/api/stages/approver-groups/{group.id}/members/{old_member.id}/substitute"
            f"?user_id={admin.id}",
            json={"new_user_id": new_member.id},
        )
        assert r.status_code == 200

        # old_member should still be able to act on the request submitted
        # before the substitution, since the snapshot is frozen.
        act_r = client.post(
            f"/api/approvals/?user_id={old_member.id}",
            json={"request_id": req_id, "decision": "approved"},
        )
        assert act_r.status_code == 200


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
