"""
tests/test_analytics.py — Analytics endpoints
tests/test_in_app_action.py — In-app approve/reject via /api/requests/action/{req_id}
tests/test_email_action.py — One-click email token approve/reject
tests/test_misc.py — Health check and edge cases
"""
import pytest
import models
from conftest import make_user, make_group, make_workflow, make_request


# ─────────────────────────────────────────────────────────────────────────────
# Analytics — Summary
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsSummary:
    def test_summary_empty_db(self, client, db):
        admin = make_user(db, email="ase@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/summary?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] == 0
        assert data["approval_rate"] == 0.0

    def test_summary_counts_correct(self, client, db):
        admin = make_user(db, email="ascc@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="asccs@x.com")
        approver = make_user(db, email="ascca@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        pending_req = make_request(db, submitter, wf, title="Pending")
        approved_req = make_request(db, submitter, wf, title="Approved")
        approved_req.status = models.RequestStatus.approved
        db.commit()
        r = client.get(f"/api/analytics/summary?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["total_requests"] >= 2
        assert data["approved"] >= 1
        assert data["pending"] >= 1

    def test_summary_workflow_filter(self, client, db):
        admin = make_user(db, email="aswf@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="aswfs@x.com")
        group = make_group(db)
        wf1 = make_workflow(db, admin, group, name="WF One")
        wf2 = make_workflow(db, admin, group, name="WF Two")
        make_request(db, submitter, wf1)
        make_request(db, submitter, wf2)
        db.commit()
        r = client.get(f"/api/analytics/summary?user_id={admin.id}&workflow_id={wf1.id}")
        assert r.status_code == 200
        assert r.json()["total_requests"] == 1

    def test_summary_days_param(self, client, db):
        admin = make_user(db, email="asdp@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/summary?user_id={admin.id}&days=7")
        assert r.status_code == 200

    def test_summary_invalid_days(self, client, db):
        admin = make_user(db, email="asid@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/summary?user_id={admin.id}&days=0")
        assert r.status_code == 422
        r = client.get(f"/api/analytics/summary?user_id={admin.id}&days=366")
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Analytics — By-workflow
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsByWorkflow:
    def test_by_workflow_returns_list(self, client, db):
        admin = make_user(db, email="abw@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="abws@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        make_request(db, submitter, wf)
        db.commit()
        r = client.get(f"/api/analytics/by-workflow?user_id={admin.id}")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_by_workflow_empty_excluded(self, client, db):
        """Workflows with zero requests in the period are excluded."""
        admin = make_user(db, email="abwe@x.com", role=models.UserRole.admin)
        group = make_group(db)
        make_workflow(db, admin, group, name="Empty WF")
        db.commit()
        r = client.get(f"/api/analytics/by-workflow?user_id={admin.id}")
        assert r.status_code == 200
        names = [w["workflow"] for w in r.json()]
        assert "Empty WF" not in names


# ─────────────────────────────────────────────────────────────────────────────
# Analytics — Approver performance
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsApproverPerformance:
    def test_approver_performance_returns_list(self, client, db):
        admin = make_user(db, email="apf@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/approver-performance?user_id={admin.id}")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_approver_performance_data_driven(self, client, db):
        """Verify the grouping, decision counts, and avg_response_hours
        math with real ApprovalAction rows. This replaces the empty-list
        smoke test with a genuine calculation check."""
        from datetime import datetime, timedelta
        admin = make_user(db, email="apfdata@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email="apfa1@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email="apfa2@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="apfs@x.com")
        group = make_group(db, members=[a1, a2])
        wf = make_workflow(db, admin, group, voting_rule=models.VotingRule.all)
        req = make_request(db, submitter, wf)
        db.commit()

        rs = req.stages[0]
        # Backdate stage start so response_seconds > 0
        rs.started_at = datetime.utcnow() - timedelta(hours=2)
        db.flush()

        # a1 approves, a2 rejects
        db.add(models.ApprovalAction(
            request_stage_id=rs.id, approver_id=a1.id,
            decision=models.ApprovalDecision.approved,
        ))
        db.add(models.ApprovalAction(
            request_stage_id=rs.id, approver_id=a2.id,
            decision=models.ApprovalDecision.rejected,
        ))
        db.commit()

        r = client.get(f"/api/analytics/approver-performance?user_id={admin.id}")
        assert r.status_code == 200
        rows = {row["approver_id"]: row for row in r.json()}

        assert a1.id in rows
        assert rows[a1.id]["approved"] == 1
        assert rows[a1.id]["rejected"] == 0
        assert rows[a1.id]["total_decisions"] == 1
        # avg_response_hours should be > 0 (stage started 2 hours ago)
        assert rows[a1.id]["avg_response_hours"] is not None
        assert rows[a1.id]["avg_response_hours"] > 0

        assert a2.id in rows
        assert rows[a2.id]["rejected"] == 1
        assert rows[a2.id]["approved"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Analytics — Activity feed
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyticsActivityFeed:
    def test_activity_feed_returns_list(self, client, db):
        admin = make_user(db, email="aaf@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/activity-feed?user_id={admin.id}")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_activity_feed_limit(self, client, db):
        admin = make_user(db, email="aafl@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/activity-feed?user_id={admin.id}&limit=5")
        assert r.status_code == 200
        assert len(r.json()) <= 5

    def test_activity_feed_limit_over_max(self, client, db):
        admin = make_user(db, email="aaflm@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/activity-feed?user_id={admin.id}&limit=999")
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# Analytics — Notification report
# ─────────────────────────────────────────────────────────────────────────────

class TestNotificationReport:

    def _setup(self, db, *, wf_name="NR Workflow"):
        """Create admin, approver, group, workflow, request, and a started stage."""
        admin    = make_user(db, email=f"nra-{wf_name}@x.com", role=models.UserRole.admin)
        approver = make_user(db, email=f"nrapp-{wf_name}@x.com", role=models.UserRole.approver)
        submitter= make_user(db, email=f"nrs-{wf_name}@x.com")
        group    = make_group(db, members=[approver])
        wf       = make_workflow(db, admin, group, name=wf_name)
        req      = make_request(db, submitter, wf)
        db.commit()
        # The one RequestStage created by make_request
        rs = db.query(models.RequestStage).filter(
            models.RequestStage.request_id == req.id
        ).first()
        return admin, approver, wf, req, rs

    def test_report_shape(self, client, db):
        """Empty DB → report returns zeros with the right keys."""
        admin = make_user(db, email="nrshape@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        for key in (
            "period_days", "total_stages_run", "total_bypassed",
            "total_received_reminders", "total_still_pending",
            "overall_bypass_rate_pct", "by_workflow",
        ):
            assert key in data, f"Missing key: {key}"

    def test_bypassed_when_no_reminder_log(self, client, db):
        """
        A stage with no 'reminder_sent' ActivityLog entry counts as bypassed —
        meaning the approver acted before any reminder was needed.
        """
        admin, approver, wf, req, rs = self._setup(db, wf_name="BypassWF")
        # Stage is still pending but has never had a reminder sent
        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["total_stages_run"] >= 1
        assert data["total_bypassed"] >= 1
        assert data["total_received_reminders"] == 0

    def test_received_reminder_when_log_exists(self, client, db):
        """
        Manually inserting a 'reminder_sent' ActivityLog entry for a stage
        should move it from bypassed → received_reminders.
        """
        admin, approver, wf, req, rs = self._setup(db, wf_name="ReminderWF")
        # Simulate what the scheduler writes
        db.add(models.ActivityLog(
            request_id=req.id,
            user_id=approver.id,
            action="reminder_sent",
            detail=f"Reminder sent to user #{approver.id} for stage 'Stage 1' (order 1)",
            stage_order=1,
            extra={
                "request_stage_id": rs.id,
                "stage_name": "Stage 1",
                "workflow_id": wf.id,
                "workflow_name": wf.name,
                "reminded_user_id": approver.id,
            },
        ))
        db.commit()

        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["total_received_reminders"] >= 1
        assert data["total_bypassed"] == 0

    def test_still_pending_after_reminder(self, client, db):
        """
        Stage received a reminder and is still pending →
        counted in both received_reminders and still_pending_after_reminder.
        """
        admin, approver, wf, req, rs = self._setup(db, wf_name="StillPendingWF")
        db.add(models.ActivityLog(
            request_id=req.id,
            user_id=approver.id,
            action="reminder_sent",
            detail="reminder",
            stage_order=1,
            extra={"request_stage_id": rs.id, "workflow_id": wf.id,
                   "workflow_name": wf.name, "stage_name": "Stage 1",
                   "reminded_user_id": approver.id},
        ))
        db.commit()
        # Stage remains pending (no approval action taken)
        assert rs.status == models.RequestStatus.pending

        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["total_still_pending"] >= 1

    def test_resolved_after_reminder_not_still_pending(self, client, db):
        """
        Stage that received a reminder but was subsequently resolved should NOT
        appear in still_pending_after_reminder.
        """
        admin, approver, wf, req, rs = self._setup(db, wf_name="ResolvedAfterReminderWF")
        db.add(models.ActivityLog(
            request_id=req.id,
            user_id=approver.id,
            action="reminder_sent",
            detail="reminder",
            stage_order=1,
            extra={"request_stage_id": rs.id, "workflow_id": wf.id,
                   "workflow_name": wf.name, "stage_name": "Stage 1",
                   "reminded_user_id": approver.id},
        ))
        # Mark stage as resolved after the reminder
        rs.status = models.RequestStatus.approved
        db.commit()

        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["total_received_reminders"] >= 1
        assert data["total_still_pending"] == 0

    def test_workflow_filter(self, client, db):
        """workflow_id query param scopes the report to one workflow."""
        admin    = make_user(db, email="nrwff@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="nrwffa@x.com", role=models.UserRole.approver)
        submitter= make_user(db, email="nrwffs@x.com")
        group    = make_group(db, members=[approver])
        wf1 = make_workflow(db, admin, group, name="NR-WF1")
        wf2 = make_workflow(db, admin, group, name="NR-WF2")
        make_request(db, submitter, wf1)
        make_request(db, submitter, wf2)
        db.commit()

        r = client.get(
            f"/api/analytics/notification-report?user_id={admin.id}&workflow_id={wf1.id}"
        )
        assert r.status_code == 200
        data = r.json()
        # Only stages from wf1 should appear
        wf_ids = [row["workflow_id"] for row in data["by_workflow"]]
        assert wf1.id in wf_ids
        assert wf2.id not in wf_ids

    def test_by_workflow_breakdown_present(self, client, db):
        """by_workflow array contains the right fields."""
        admin, _, wf, req, rs = self._setup(db, wf_name="BreakdownWF")
        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}")
        assert r.status_code == 200
        data = r.json()
        if data["by_workflow"]:
            row = data["by_workflow"][0]
            for field in (
                "workflow_id", "workflow_name", "total_stages_run",
                "bypassed_notifications", "received_reminders",
                "still_pending_after_reminder", "bypass_rate_pct",
            ):
                assert field in row, f"Missing field in by_workflow row: {field}"

    def test_bypass_rate_calculation(self, client, db):
        """bypass_rate_pct = bypassed / total * 100."""
        admin, approver, wf, req, rs = self._setup(db, wf_name="RateWF")
        # One stage with no reminder → 100% bypass
        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}")
        data = r.json()
        wf_row = next(
            (row for row in data["by_workflow"] if row["workflow_id"] == wf.id), None
        )
        if wf_row:
            assert wf_row["bypass_rate_pct"] == 100.0

    def test_days_param(self, client, db):
        admin = make_user(db, email="nrdp@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}&days=7")
        assert r.status_code == 200
        assert r.json()["period_days"] == 7

    def test_invalid_days(self, client, db):
        admin = make_user(db, email="nrid@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.get(f"/api/analytics/notification-report?user_id={admin.id}&days=0")
        assert r.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# In-app action endpoint (/api/requests/action/{req_id})
# ─────────────────────────────────────────────────────────────────────────────

class TestInAppAction:
    def _setup(self, db):
        admin = make_user(db, email="iaa@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="iaaa@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="iaas@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        return req, approver, submitter

    def test_in_app_approve(self, client, db):
        req, approver, _ = self._setup(db)
        r = client.post(
            f"/api/requests/action/{req.id}?user_id={approver.id}&action=approved"
        )
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

    def test_in_app_reject(self, client, db):
        req, approver, _ = self._setup(db)
        r = client.post(
            f"/api/requests/action/{req.id}?user_id={approver.id}&action=rejected"
        )
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.rejected

    def test_in_app_invalid_action(self, client, db):
        req, approver, _ = self._setup(db)
        r = client.post(
            f"/api/requests/action/{req.id}?user_id={approver.id}&action=maybe"
        )
        assert r.status_code == 400

    def test_in_app_non_member_blocked(self, client, db):
        req, _, _ = self._setup(db)
        outsider = make_user(db, email="iaob@x.com", role=models.UserRole.approver)
        db.commit()
        r = client.post(
            f"/api/requests/action/{req.id}?user_id={outsider.id}&action=approved"
        )
        assert r.status_code == 403

    def test_in_app_already_resolved(self, client, db):
        req, approver, _ = self._setup(db)
        req.status = models.RequestStatus.approved
        db.commit()
        r = client.post(
            f"/api/requests/action/{req.id}?user_id={approver.id}&action=approved"
        )
        assert r.status_code == 400

    def test_in_app_duplicate_blocked(self, client, db):
        req, approver, _ = self._setup(db)
        client.post(f"/api/requests/action/{req.id}?user_id={approver.id}&action=approved")
        r = client.post(f"/api/requests/action/{req.id}?user_id={approver.id}&action=approved")
        assert r.status_code in (400, 404)

    def test_in_app_with_comment(self, client, db):
        req, approver, _ = self._setup(db)
        r = client.post(
            f"/api/requests/action/{req.id}?user_id={approver.id}&action=approved&comment=LGTM"
        )
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Email one-click token action (/api/requests/action/{token})
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailTokenAction:
    def _setup(self, db):
        admin = make_user(db, email="eta@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="etaa@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="etas@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        return req, approver

    def _token(self, req, approver, action="approved"):
        from auth_utils import create_approval_token
        return create_approval_token(req.id, 1, action, approver.id)

    def test_email_approve(self, client, db):
        req, approver = self._setup(db)
        token = self._token(req, approver, "approved")
        r = client.get(f"/api/requests/action/{token}")
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

    def test_email_reject(self, client, db):
        req, approver = self._setup(db)
        token = self._token(req, approver, "rejected")
        r = client.get(f"/api/requests/action/{token}")
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.rejected

    def test_email_invalid_token(self, client):
        r = client.get("/api/requests/action/bad.token.here")
        assert r.status_code == 401

    def test_email_already_resolved(self, client, db):
        req, approver = self._setup(db)
        req.status = models.RequestStatus.approved
        db.commit()
        token = self._token(req, approver, "approved")
        r = client.get(f"/api/requests/action/{token}")
        assert r.status_code == 400

    def test_email_non_member_token(self, client, db):
        req, _ = self._setup(db)
        outsider = make_user(db, email="etao@x.com", role=models.UserRole.approver)
        db.commit()
        from auth_utils import create_approval_token
        token = create_approval_token(req.id, 1, "approved", outsider.id)
        r = client.get(f"/api/requests/action/{token}")
        assert r.status_code == 400

    def test_email_double_click_blocked(self, client, db):
        req, approver = self._setup(db)
        token = self._token(req, approver, "approved")
        client.get(f"/api/requests/action/{token}")
        r = client.get(f"/api/requests/action/{token}")
        assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Misc / Health
# ─────────────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestOOODelegation:
    def test_resolve_approver_follows_delegate(self, db):
        from datetime import datetime, timedelta
        from auth_utils import resolve_approver

        delegate = make_user(db, email="del@x.com", role=models.UserRole.approver)
        approver = make_user(db, email="ooo@x.com", role=models.UserRole.approver)
        approver.ooo_until = datetime.utcnow() + timedelta(days=3)
        approver.delegate_id = delegate.id
        db.flush()

        resolved = resolve_approver(approver, db)
        assert resolved.id == delegate.id

    def test_resolve_approver_returns_self_when_not_ooo(self, db):
        from auth_utils import resolve_approver
        approver = make_user(db, email="notooo@x.com", role=models.UserRole.approver)
        db.flush()
        resolved = resolve_approver(approver, db)
        assert resolved.id == approver.id

    def test_resolve_approver_max_depth(self, db):
        from datetime import datetime, timedelta
        from auth_utils import resolve_approver

        users = [make_user(db, email=f"chain{i}@x.com") for i in range(5)]
        future = datetime.utcnow() + timedelta(days=1)
        for i, u in enumerate(users[:-1]):
            u.ooo_until = future
            u.delegate_id = users[i + 1].id
        db.flush()

        result = resolve_approver(users[0], db)
        assert result is not None
