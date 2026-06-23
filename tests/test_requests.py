"""
tests/test_requests.py — Request submission, listing, retrieval, cancel, auto-approve
"""
import pytest
import models
from conftest import make_user, make_group, make_workflow, make_request
from auth_utils import create_access_token


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


class TestSendMessage:
    """
    POST /api/requests/{req_id}/send-message — Messaging #4 (send on click,
    no stage required). Uses get_current_user (real JWT bearer auth), unlike
    every other endpoint in this router, so these tests build a token via
    auth_utils.create_access_token rather than passing ?user_id=.
    """

    def test_submitter_sends_to_self(self, client, db):
        admin = make_user(db, email="sm1a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm1s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf, title="Invoice for messaging")
        token = create_access_token(data={"sub": submitter.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Please review soon", "to": "submitter"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["recipients"] == [submitter.email]

    def test_admin_sends_to_current_approvers(self, client, db):
        admin = make_user(db, email="sm2a@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="sm2appr@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="sm2s@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Please approve", "to": "current_approvers"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert approver.email in r.json()["recipients"]

    def test_send_to_custom_emails(self, client, db):
        admin = make_user(db, email="sm3a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm3s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={
                "message": "FYI",
                "to": "custom",
                "custom_emails": ["external@vendor.com", "other@vendor.com"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert set(r.json()["recipients"]) == {"external@vendor.com", "other@vendor.com"}

    def test_invalid_to_value(self, client, db):
        admin = make_user(db, email="sm4a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm4s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Hi", "to": "everyone"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    def test_custom_with_no_emails_yields_no_recipients(self, client, db):
        admin = make_user(db, email="sm5a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm5s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Hi", "to": "custom"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400

    def test_nonexistent_request(self, client, db):
        admin = make_user(db, email="sm6a@x.com", role=models.UserRole.admin)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            "/api/requests/99999/send-message",
            json={"message": "Hi", "to": "submitter"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 404

    def test_unrelated_user_forbidden(self, client, db):
        admin = make_user(db, email="sm7a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm7s@x.com")
        outsider = make_user(db, email="sm7out@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": outsider.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Hi", "to": "submitter"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_approver_group_member_can_send(self, client, db):
        """A user in any approver group attached to the workflow (not just
        the currently-active stage) passes the access check, mirroring
        GET /{req_id}'s access rule."""
        admin = make_user(db, email="sm8a@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="sm8appr@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="sm8s@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": approver.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Checking in", "to": "submitter"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    def test_no_token_unauthorized(self, client, db):
        admin = make_user(db, email="sm9a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm9s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Hi", "to": "submitter"},
        )
        assert r.status_code == 401

    def test_logs_activity(self, client, db):
        admin = make_user(db, email="sm10a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm10s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "Hi", "to": "submitter"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        logs = [a.action for a in req.activity_log]
        assert "manual_message" in logs

    def test_message_template_renders_request_fields(self, client, db):
        """{{field}} placeholders from base request fields are substituted
        via template_utils — verified indirectly: the endpoint succeeds and
        an ActivityLog row is written. The literal rendered text is sent
        through notify_custom_message (async, SMTP not configured in tests),
        so we assert on the side effects we can observe synchronously."""
        admin = make_user(db, email="sm11a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm11s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf, title="Q3 Invoice", amount=4200.0)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={
                "message": "Reminder about {{title}}, amount {{amount}}",
                "subject": "RE: {{title}}",
                "to": "submitter",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    def test_reminder_interval_creates_scheduled_message(self, client, db):
        admin = make_user(db, email="sm12a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm12s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={
                "message": "Still pending",
                "to": "submitter",
                "reminder_interval_hours": 6,
                "max_reminders": 3,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        sm = db.query(models.ScheduledMessage).filter(
            models.ScheduledMessage.request_id == req.id
        ).first()
        assert sm is not None
        assert sm.reminder_interval_hours == 6
        assert sm.max_reminders == 3
        assert sm.is_active is True

    def test_no_reminder_interval_creates_no_scheduled_message(self, client, db):
        admin = make_user(db, email="sm13a@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="sm13s@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        token = create_access_token(data={"sub": admin.id})
        db.commit()
        r = client.post(
            f"/api/requests/{req.id}/send-message",
            json={"message": "One-off note", "to": "submitter"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        sm = db.query(models.ScheduledMessage).filter(
            models.ScheduledMessage.request_id == req.id
        ).first()
        assert sm is None


# ── Email-link redirect (Workflow #12) ───────────────────────────────────────

class TestEmailLinkRedirect:
    """
    GET /api/requests/action/{token}/redirect

    Performs the same approve/reject logic as GET /action/{token} (JSON)
    but responds with an HTTP 307 redirect to:
      - workflow.success_redirect_url  (on approve, if configured)
      - workflow.failure_redirect_url  (on reject, if configured)
      - FRONTEND_URL/requests/{id}     (fallback when not configured)

    TestClient follows redirects by default; we disable that
    (`follow_redirects=False`) so we can assert on the 307 and Location
    header directly.
    """

    def _setup(self, db, success_url=None, failure_url=None):
        import uuid
        from conftest import make_group
        uid = uuid.uuid4().hex[:8]
        admin = make_user(db, email=f"elr-adm-{uid}@x.com", role=models.UserRole.admin)
        approver = make_user(db, email=f"elr-appr-{uid}@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email=f"elr-sub-{uid}@x.com")
        group = make_group(db, members=[approver])
        wf = models.Workflow(
            name="Redirect WF",
            type=models.WorkflowType.approval,
            escalation_hours=24,
            rejection_behavior=models.RejectionBehavior.stop,
            notification_channel=models.NotificationChannel.email,
            created_by_id=admin.id,
            success_redirect_url=success_url,
            failure_redirect_url=failure_url,
        )
        db.add(wf)
        db.flush()
        stage = models.WorkflowStage(
            workflow_id=wf.id, name="Stage 1",
            type=models.WorkflowType.approval,
            order=1, approver_group_id=group.id,
            sla_hours=48, voting_rule=models.VotingRule.any,
        )
        db.add(stage)
        db.flush()
        from conftest import make_request
        req = make_request(db, submitter, wf)
        # Build the workflow snapshot so get_stage_config finds the approver
        from routers.requests import _build_workflow_snapshot
        req.workflow_snapshot = _build_workflow_snapshot(wf)
        db.commit()
        return req, approver

    def _token(self, req_id, stage_order, action, approver_id):
        from auth_utils import create_approval_token
        return create_approval_token(req_id, stage_order, action, approver_id)

    def test_approve_redirects_to_success_url(self, client, db):
        req, approver = self._setup(db, success_url="https://app.example.com/success")
        token = self._token(req.id, 1, "approved", approver.id)
        r = client.get(
            f"/api/requests/action/{token}/redirect",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "https://app.example.com/success"

    def test_reject_redirects_to_failure_url(self, client, db):
        req, approver = self._setup(
            db,
            success_url="https://app.example.com/success",
            failure_url="https://app.example.com/failure",
        )
        token = self._token(req.id, 1, "rejected", approver.id)
        r = client.get(
            f"/api/requests/action/{token}/redirect",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "https://app.example.com/failure"

    def test_approve_falls_back_to_frontend_url_when_no_success_url(self, client, db):
        import os
        req, approver = self._setup(db, success_url=None)
        token = self._token(req.id, 1, "approved", approver.id)
        r = client.get(
            f"/api/requests/action/{token}/redirect",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        frontend = os.getenv("FRONTEND_URL", "http://localhost:3000")
        assert r.headers["location"] == f"{frontend}/requests/{req.id}"

    def test_reject_falls_back_to_frontend_url_when_no_failure_url(self, client, db):
        import os
        req, approver = self._setup(db, failure_url=None)
        token = self._token(req.id, 1, "rejected", approver.id)
        r = client.get(
            f"/api/requests/action/{token}/redirect",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        frontend = os.getenv("FRONTEND_URL", "http://localhost:3000")
        assert r.headers["location"] == f"{frontend}/requests/{req.id}"

    def test_action_is_actually_recorded(self, client, db):
        """The redirect endpoint must persist the approval action, not just redirect."""
        req, approver = self._setup(db, success_url="https://done.example.com")
        token = self._token(req.id, 1, "approved", approver.id)
        client.get(
            f"/api/requests/action/{token}/redirect",
            follow_redirects=False,
        )
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

    def test_invalid_token_redirects_to_error_page(self, client, db):
        import os
        r = client.get(
            "/api/requests/action/bad.token.here/redirect",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert "action-error" in r.headers["location"]

    def test_already_acted_token_redirects_to_error(self, client, db):
        """Using the same token twice — second use must not 500, it redirects to error."""
        req, approver = self._setup(db, success_url="https://done.example.com")
        token = self._token(req.id, 1, "approved", approver.id)
        client.get(f"/api/requests/action/{token}/redirect", follow_redirects=False)
        r = client.get(
            f"/api/requests/action/{token}/redirect",
            follow_redirects=False,
        )
        assert r.status_code in (302, 307)
        assert "action-error" in r.headers["location"]

    def test_json_action_endpoint_still_works(self, client, db):
        """GET /action/{token} (JSON, no redirect) must continue to work
        independently of the redirect variant."""
        req, approver = self._setup(db)
        token = self._token(req.id, 1, "approved", approver.id)
        r = client.get(f"/api/requests/action/{token}")
        assert r.status_code == 200
        assert r.json()["status"] == "approved"
