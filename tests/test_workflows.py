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


class TestMessageVariables:
    """Messaging #3 — derived formula variables stored on Workflow.message_variables.
    See template_utils.py for the actual evaluator's unit tests."""

    def _group_and_admin(self, db):
        admin = make_user(db, email="mva@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        return admin, group

    def test_create_with_message_variables(self, client, db):
        admin, group = self._group_and_admin(db)
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "WF With Formulas", "type": "approval",
            "message_variables": [
                {"name": "tax", "formula": "amount * 0.18"},
                {"name": "total", "formula": "amount + tax"},
            ],
            "stages": [{
                "name": "Review", "type": "approval",
                "order": 1, "approver_group_id": group.id
            }]
        })
        assert r.status_code == 200
        mv = r.json()["message_variables"]
        assert {"name": "tax", "formula": "amount * 0.18"} in mv
        assert {"name": "total", "formula": "amount + tax"} in mv

    def test_create_without_message_variables_defaults_none(self, client, db):
        admin, group = self._group_and_admin(db)
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "WF No Formulas", "type": "approval",
            "stages": [{
                "name": "Review", "type": "approval",
                "order": 1, "approver_group_id": group.id
            }]
        })
        assert r.status_code == 200
        assert r.json()["message_variables"] is None

    def test_update_sets_message_variables(self, client, db):
        admin, group = self._group_and_admin(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.patch(f"/api/workflows/{wf.id}?user_id={admin.id}", json={
            "message_variables": [{"name": "discount", "formula": "amount * 0.1"}]
        })
        assert r.status_code == 200
        assert r.json()["message_variables"] == [{"name": "discount", "formula": "amount * 0.1"}]

    def test_invalid_formula_shape_rejected_by_schema(self, client, db):
        """MessageVariable requires both name and formula — missing formula
        should fail Pydantic validation before it ever reaches the DB."""
        admin, group = self._group_and_admin(db)
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "WF Bad Formula", "type": "approval",
            "message_variables": [{"name": "tax"}],
            "stages": [{
                "name": "Review", "type": "approval",
                "order": 1, "approver_group_id": group.id
            }]
        })
        assert r.status_code == 422


# ── Stage button labels (Workflow #7) ──────────────────────────────────────────

class TestStageButtonLabels:
    """Workflow #7 — approve_label / reject_label on WorkflowStage let
    admins rename the action buttons per stage (e.g. 'Confirm' / 'Return').
    Labels are stored on WorkflowStage, returned in the workflow detail
    response, and can be updated via the stage-edit endpoints."""

    def test_create_workflow_with_custom_labels(self, client, db):
        admin = make_user(db, email="bl1a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "Label WF", "type": "approval",
            "stages": [{
                "name": "Legal Review", "type": "approval",
                "order": 1, "approver_group_id": group.id,
                "approve_label": "Confirm", "reject_label": "Return",
            }]
        })
        assert r.status_code == 200
        stage = r.json()["stages"][0]
        assert stage["approve_label"] == "Confirm"
        assert stage["reject_label"] == "Return"

    def test_default_labels_are_null_when_not_set(self, client, db):
        admin = make_user(db, email="bl2a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "Default Label WF", "type": "approval",
            "stages": [{
                "name": "Stage 1", "type": "approval",
                "order": 1, "approver_group_id": group.id,
            }]
        })
        assert r.status_code == 200
        stage = r.json()["stages"][0]
        assert stage.get("approve_label") is None
        assert stage.get("reject_label") is None

    def test_update_stage_labels_via_workflow_patch(self, client, db):
        admin = make_user(db, email="bl3a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.patch(f"/api/workflows/{wf.id}?user_id={admin.id}", json={
            "stages": [{
                "name": "Stage 1", "type": "approval",
                "order": 1, "approver_group_id": group.id,
                "approve_label": "Authorise", "reject_label": "Send Back",
            }]
        })
        assert r.status_code == 200
        stage = r.json()["stages"][0]
        assert stage["approve_label"] == "Authorise"
        assert stage["reject_label"] == "Send Back"

    def test_labels_stored_in_db_on_stage_row(self, client, db):
        admin = make_user(db, email="bl4a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "DB Label WF", "type": "approval",
            "stages": [{
                "name": "Sign-off", "type": "approval",
                "order": 1, "approver_group_id": group.id,
                "approve_label": "Sign Off", "reject_label": "Decline",
            }]
        })
        assert r.status_code == 200
        wf_id = r.json()["id"]
        stage = db.query(models.WorkflowStage).filter(
            models.WorkflowStage.workflow_id == wf_id
        ).first()
        assert stage.approve_label == "Sign Off"
        assert stage.reject_label == "Decline"

    def test_labels_appear_in_stage_config_for_notification(
        self, client, db
    ):
        """The frozen workflow_snapshot used by the notification path carries
        approve_label / reject_label so email buttons reflect the custom text
        even after the workflow is later edited."""
        from routers.requests import _build_workflow_snapshot
        admin = make_user(db, email="bl5a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "Snapshot Label WF", "type": "approval",
            "stages": [{
                "name": "Review", "type": "approval",
                "order": 1, "approver_group_id": group.id,
                "approve_label": "Accept", "reject_label": "Reject",
            }]
        })
        wf = db.query(models.Workflow).filter(
            models.Workflow.id == r.json()["id"]
        ).first()
        snapshot = _build_workflow_snapshot(wf)
        stage_snap = snapshot["stages"][0]
        assert stage_snap.get("approve_label") == "Accept"
        assert stage_snap.get("reject_label") == "Reject"


# ── Redirect URLs on success/failure (Workflow #12) ─────────────────────────

class TestRedirectUrls:
    """Workflow #12 — success_redirect_url / failure_redirect_url on Workflow
    tell the frontend where to send the user after a one-click email action
    resolves. These are stored and returned in the workflow detail."""

    def test_create_with_redirect_urls(self, client, db):
        admin = make_user(db, email="ru1a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "Redirect WF", "type": "approval",
            "success_redirect_url": "https://app.example.com/success",
            "failure_redirect_url": "https://app.example.com/rejected",
            "stages": [{
                "name": "Review", "type": "approval",
                "order": 1, "approver_group_id": group.id
            }]
        })
        assert r.status_code == 200
        data = r.json()
        assert data["success_redirect_url"] == "https://app.example.com/success"
        assert data["failure_redirect_url"] == "https://app.example.com/rejected"

    def test_redirect_urls_default_to_null(self, client, db):
        admin = make_user(db, email="ru2a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.get(f"/api/workflows/{wf.id}?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        assert data.get("success_redirect_url") is None
        assert data.get("failure_redirect_url") is None

    def test_update_redirect_urls(self, client, db):
        admin = make_user(db, email="ru3a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        db.commit()
        r = client.patch(f"/api/workflows/{wf.id}?user_id={admin.id}", json={
            "success_redirect_url": "https://app.example.com/done",
            "failure_redirect_url": "https://app.example.com/fail",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["success_redirect_url"] == "https://app.example.com/done"
        assert data["failure_redirect_url"] == "https://app.example.com/fail"

    def test_redirect_urls_stored_in_db(self, client, db):
        admin = make_user(db, email="ru4a@x.com", role=models.UserRole.admin)
        group = make_group(db)
        db.commit()
        r = client.post(f"/api/workflows/?user_id={admin.id}", json={
            "name": "DB Redirect WF", "type": "approval",
            "success_redirect_url": "https://example.com/ok",
            "stages": [{
                "name": "Stage 1", "type": "approval",
                "order": 1, "approver_group_id": group.id
            }]
        })
        wf_id = r.json()["id"]
        wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
        assert wf.success_redirect_url == "https://example.com/ok"
        assert wf.failure_redirect_url is None


# ── Snapshot isolation for member add/delete (Workflow #2) ───────────────────

class TestSnapshotIsolationForMemberChanges:
    """Workflow #2 — adding or removing group members after a request has
    been submitted must NOT affect who can approve that in-flight request.
    The snapshot frozen at submission time governs; only new requests see
    the updated membership. (Substitution isolation is already covered in
    test_stages.py::TestSubstituteMember; this covers plain add/remove.)"""

    def test_adding_member_after_submission_cannot_act_on_old_request(
        self, client, db
    ):
        admin = make_user(db, email="sna1@x.com", role=models.UserRole.admin)
        original = make_user(db, email="sna1o@x.com", role=models.UserRole.approver)
        newcomer = make_user(db, email="sna1n@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="sna1s@x.com")
        group = make_group(db, members=[original])
        wf = make_workflow(db, admin, group)
        db.commit()

        # Submit the request — snapshot frozen with only `original`
        sub_r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Pre-add request", "workflow_id": wf.id
        })
        assert sub_r.status_code == 201
        req_id = sub_r.json()["id"]

        # Admin adds newcomer to the live group after submission
        client.post(
            f"/api/stages/approver-groups/{group.id}/members?user_id={admin.id}",
            json={"user_id": newcomer.id, "sequential_order": 1},
        )

        # newcomer should NOT be able to act on the already-submitted request
        r = client.post(f"/api/approvals/?user_id={newcomer.id}", json={
            "request_id": req_id, "decision": "approved"
        })
        assert r.status_code == 403

    def test_adding_member_allows_them_on_new_requests(self, client, db):
        admin = make_user(db, email="sna2@x.com", role=models.UserRole.admin)
        original = make_user(db, email="sna2o@x.com", role=models.UserRole.approver)
        newcomer = make_user(db, email="sna2n@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="sna2s@x.com")
        group = make_group(db, members=[original])
        wf = make_workflow(db, admin, group)
        db.commit()

        # Add newcomer before the next request is submitted
        client.post(
            f"/api/stages/approver-groups/{group.id}/members?user_id={admin.id}",
            json={"user_id": newcomer.id, "sequential_order": 1},
        )

        sub_r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Post-add request", "workflow_id": wf.id
        })
        assert sub_r.status_code == 201
        req_id = sub_r.json()["id"]

        # newcomer IS in the group when this request is submitted — should work
        r = client.post(f"/api/approvals/?user_id={newcomer.id}", json={
            "request_id": req_id, "decision": "approved"
        })
        assert r.status_code == 200

    def test_removing_member_keeps_them_on_old_request(self, client, db):
        admin = make_user(db, email="sna3@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="sna3a@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="sna3s@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        db.commit()

        # Submit request with approver in group
        sub_r = client.post(f"/api/requests/?user_id={submitter.id}", json={
            "title": "Pre-remove request", "workflow_id": wf.id
        })
        assert sub_r.status_code == 201
        req_id = sub_r.json()["id"]

        # Admin removes approver from live group
        client.delete(
            f"/api/stages/approver-groups/{group.id}/members/{approver.id}?user_id={admin.id}"
        )

        # approver was in the group at submission time — must still be able to act
        r = client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req_id, "decision": "approved"
        })
        assert r.status_code == 200
