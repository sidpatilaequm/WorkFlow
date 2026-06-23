"""
tests/test_approvals.py — Core approval engine:
    voting rules (any/all/sequential), rejection behaviors, edge cases,
    duplicate prevention, role enforcement, OOO delegation
"""
import pytest
import models
from conftest import make_user, make_group, make_workflow, make_request


# ── Helpers ───────────────────────────────────────────────────────────────────

def approve(client, req_id, user_id, comment=None):
    payload = {"request_id": req_id, "decision": "approved"}
    if comment:
        payload["comment"] = comment
    return client.post(f"/api/approvals/?user_id={user_id}", json=payload)


def reject(client, req_id, user_id, comment=None):
    payload = {"request_id": req_id, "decision": "rejected"}
    if comment:
        payload["comment"] = comment
    return client.post(f"/api/approvals/?user_id={user_id}", json=payload)


# ── VotingRule.any ────────────────────────────────────────────────────────────

class TestVotingAny:
    def test_any_single_approver_approves(self, client, db):
        admin = make_user(db, email="vaa@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="vaaa@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="vaas@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group, voting_rule=models.VotingRule.any)
        req = make_request(db, submitter, wf)
        db.commit()

        r = approve(client, req.id, approver.id)
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved


# ── Document upload on approval/rejection (Workflow #8) ─────────────────────

class TestDocumentOnApproval:
    """Workflow #8 — an approver can attach a document (document_name /
    document_url) to their approve/reject action. The fields are stored on
    the ApprovalAction row and surfaced in the activity log detail."""

    def _setup(self, db):
        admin = make_user(db, email="doa@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="doaap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="doas@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        return req, approver

    def test_approve_with_document_stored_on_action(self, client, db):
        req, approver = self._setup(db)
        r = client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id,
            "decision": "approved",
            "document_name": "signed_copy.pdf",
            "document_url": "/uploads/signed_copy.pdf",
        })
        assert r.status_code == 200
        action_id = r.json()["id"]
        action = db.query(models.ApprovalAction).filter(
            models.ApprovalAction.id == action_id
        ).first()
        assert action.document_name == "signed_copy.pdf"
        assert action.document_url == "/uploads/signed_copy.pdf"

    def test_reject_with_document_stored(self, client, db):
        req, approver = self._setup(db)
        r = client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id,
            "decision": "rejected",
            "document_name": "rejection_memo.pdf",
            "document_url": "/uploads/rejection_memo.pdf",
        })
        assert r.status_code == 200
        action = db.query(models.ApprovalAction).filter(
            models.ApprovalAction.request_stage_id == req.stages[0].id
        ).first()
        assert action.document_url == "/uploads/rejection_memo.pdf"

    def test_document_detail_in_activity_log(self, client, db):
        """The activity log detail string must mention the attached document
        so the audit trail is complete."""
        req, approver = self._setup(db)
        client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id,
            "decision": "approved",
            "document_name": "evidence.pdf",
            "document_url": "/uploads/evidence.pdf",
        })
        db.refresh(req)
        details = [a.detail for a in req.activity_log if a.detail]
        assert any("evidence.pdf" in d or "document" in d.lower() for d in details)

    def test_action_without_document_stores_null(self, client, db):
        req, approver = self._setup(db)
        r = client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id,
            "decision": "approved",
        })
        assert r.status_code == 200
        action_id = r.json()["id"]
        action = db.query(models.ApprovalAction).filter(
            models.ApprovalAction.id == action_id
        ).first()
        assert action.document_name is None
        assert action.document_url is None

    def test_document_url_only_also_accepted(self, client, db):
        """document_url without document_name should still persist."""
        req, approver = self._setup(db)
        r = client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id,
            "decision": "approved",
            "document_url": "/uploads/anon.pdf",
        })
        assert r.status_code == 200
        action = db.query(models.ApprovalAction).filter(
            models.ApprovalAction.id == r.json()["id"]
        ).first()
        assert action.document_url == "/uploads/anon.pdf"


# ── Audit trail completeness (Compliance #1) ─────────────────────────────────

class TestAuditTrailCompleteness:
    """Compliance #1 — every significant state change in a request's lifecycle
    must produce an ActivityLog row with the correct `action` value so the
    admin's audit view is authoritative."""

    def _setup(self, db, voting_rule=models.VotingRule.any,
               rejection_behavior=models.RejectionBehavior.stop):
        admin = make_user(db, email="atc-admin@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="atc-ap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="atc-sub@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(
            db, admin, group,
            voting_rule=voting_rule,
            rejection_behavior=rejection_behavior,
        )
        req = make_request(db, submitter, wf)
        db.commit()
        return req, approver, admin

    def test_approval_action_logged(self, client, db):
        req, approver, _ = self._setup(db)
        client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        db.refresh(req)
        actions = [a.action for a in req.activity_log]
        assert "approved" in actions

    def test_rejection_action_logged(self, client, db):
        req, approver, _ = self._setup(db)
        client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id, "decision": "rejected",
        })
        db.refresh(req)
        actions = [a.action for a in req.activity_log]
        assert "rejected" in actions

    def test_cancellation_logged(self, client, db):
        req, _, _ = self._setup(db)
        submitter_id = req.submitter_id
        client.patch(f"/api/requests/{req.id}/cancel?user_id={submitter_id}")
        db.refresh(req)
        actions = [a.action for a in req.activity_log]
        assert "cancelled" in actions

    def test_escalation_logged(self, client, db):
        """SLA breach → escalation must appear in the activity log."""
        from datetime import datetime, timedelta
        import services.escalation as escalation
        req, approver, _ = self._setup(db)
        req.stages[0].sla_deadline = datetime.utcnow() - timedelta(hours=1)
        db.commit()
        escalation.run_escalation_check(db)
        db.refresh(req)
        actions = [a.action for a in req.activity_log]
        assert "escalated" in actions

    def test_multi_stage_advance_logged_per_stage(self, client, db):
        """Each stage completion in a multi-stage workflow must produce its
        own ActivityLog entry (one per advance), not a single bulk entry."""
        from datetime import datetime, timedelta
        admin = make_user(db, email="atcms-admin@x.com", role=models.UserRole.admin)
        s1_approver = make_user(db, email="atcms-s1@x.com", role=models.UserRole.approver)
        s2_approver = make_user(db, email="atcms-s2@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="atcms-sub@x.com")
        group1 = make_group(db, name="ATC Group 1", members=[s1_approver])
        group2 = make_group(db, name="ATC Group 2", members=[s2_approver])
        wf = models.Workflow(
            name="ATC Multi WF", type=models.WorkflowType.approval,
            escalation_hours=24, rejection_behavior=models.RejectionBehavior.stop,
            notification_channel=models.NotificationChannel.email,
            created_by_id=admin.id,
        )
        db.add(wf)
        db.flush()
        stage1 = models.WorkflowStage(
            workflow_id=wf.id, name="Stage 1", type=models.WorkflowType.approval,
            order=1, approver_group_id=group1.id, sla_hours=48,
            voting_rule=models.VotingRule.any, is_optional=False,
        )
        stage2 = models.WorkflowStage(
            workflow_id=wf.id, name="Stage 2", type=models.WorkflowType.approval,
            order=2, approver_group_id=group2.id, sla_hours=48,
            voting_rule=models.VotingRule.any, is_optional=False,
        )
        db.add_all([stage1, stage2])
        db.flush()
        req = models.WorkflowRequest(
            title="ATC Multi Request", workflow_id=wf.id,
            submitter_id=submitter.id, status=models.RequestStatus.pending,
            current_stage=1,
        )
        db.add(req)
        db.flush()
        now = datetime.utcnow()
        rs1 = models.RequestStage(
            request_id=req.id, stage_id=stage1.id, stage_order=1,
            status=models.RequestStatus.pending, started_at=now,
            sla_deadline=now + timedelta(hours=48),
        )
        rs2 = models.RequestStage(
            request_id=req.id, stage_id=stage2.id, stage_order=2,
            status=models.RequestStatus.pending,
        )
        db.add_all([rs1, rs2])
        db.commit()

        client.post(f"/api/approvals/?user_id={s1_approver.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        client.post(f"/api/approvals/?user_id={s2_approver.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        db.refresh(req)
        assert req.status == models.RequestStatus.approved
        # Both stage approvals must be individually logged
        approved_logs = [a for a in req.activity_log if a.action == "approved"]
        assert len(approved_logs) >= 2

    def test_any_one_of_two_approvers_sufficient(self, client, db):
        admin = make_user(db, email="vao@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email="vaoa1@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email="vaoa2@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="vaos@x.com")
        group = make_group(db, members=[a1, a2])
        wf = make_workflow(db, admin, group, voting_rule=models.VotingRule.any)
        req = make_request(db, submitter, wf)
        db.commit()

        r = approve(client, req.id, a1.id)
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved


# ── VotingRule.all ────────────────────────────────────────────────────────────

class TestVotingAll:
    def test_all_requires_both_approvers(self, client, db):
        admin = make_user(db, email="vala@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email="vala1@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email="vala2@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="vals@x.com")
        group = make_group(db, members=[a1, a2])
        wf = make_workflow(db, admin, group, voting_rule=models.VotingRule.all)
        req = make_request(db, submitter, wf)
        db.commit()

        # First approval — should still be pending
        r = approve(client, req.id, a1.id)
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.pending

        # Second approval — now complete
        r = approve(client, req.id, a2.id)
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

    def test_all_single_approver_completes_immediately(self, client, db):
        admin = make_user(db, email="vasi@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="vasia@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="vasis@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group, voting_rule=models.VotingRule.all)
        req = make_request(db, submitter, wf)
        db.commit()

        r = approve(client, req.id, approver.id)
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved


# ── VotingRule.sequential ─────────────────────────────────────────────────────

class TestVotingSequential:
    def _setup(self, db):
        admin = make_user(db, email="vsadm@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email="vsa1@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email="vsa2@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="vss@x.com")
        group = make_group(db, members=[a1, a2])  # sequential_order 0, 1
        wf = make_workflow(db, admin, group, voting_rule=models.VotingRule.sequential)
        req = make_request(db, submitter, wf)
        db.commit()
        return req, a1, a2

    def test_sequential_first_approver_goes_first(self, client, db):
        req, a1, a2 = self._setup(db)
        r = approve(client, req.id, a1.id)
        assert r.status_code == 200

    def test_sequential_second_approver_blocked_until_first(self, client, db):
        req, a1, a2 = self._setup(db)
        # a2 tries to go first — should be 403
        r = approve(client, req.id, a2.id)
        assert r.status_code == 403

    def test_sequential_full_chain(self, client, db):
        req, a1, a2 = self._setup(db)
        approve(client, req.id, a1.id)
        db.refresh(req)
        assert req.status == models.RequestStatus.pending  # still needs a2
        approve(client, req.id, a2.id)
        db.refresh(req)
        assert req.status == models.RequestStatus.approved


# ── Rejection Behaviors ───────────────────────────────────────────────────────

class TestRejectionStop:
    def test_rejection_stops_workflow(self, client, db):
        admin = make_user(db, email="rsa@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="rsap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="rss@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group, rejection_behavior=models.RejectionBehavior.stop)
        req = make_request(db, submitter, wf)
        db.commit()

        r = reject(client, req.id, approver.id)
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.rejected


class TestRejectionEscalate:
    def test_rejection_escalates(self, client, db):
        admin = make_user(db, email="rea@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="reap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="res@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group, rejection_behavior=models.RejectionBehavior.escalate)
        req = make_request(db, submitter, wf)
        db.commit()

        r = reject(client, req.id, approver.id)
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.escalated


class TestRejectionRestart:
    def test_rejection_restarts_workflow(self, client, db):
        admin = make_user(db, email="rra@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="rrap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="rrs@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group, rejection_behavior=models.RejectionBehavior.restart)
        req = make_request(db, submitter, wf)
        db.commit()

        r = reject(client, req.id, approver.id)
        assert r.status_code == 200
        db.refresh(req)
        # After restart current_stage resets and stages are re-opened
        assert req.status == models.RequestStatus.pending


# ── Duplicate Prevention ──────────────────────────────────────────────────────

class TestDuplicatePrevention:
    def test_cannot_approve_twice(self, client, db):
        admin = make_user(db, email="dpa@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="dpap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="dps@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()

        approve(client, req.id, approver.id)
        r = approve(client, req.id, approver.id)  # second time
        assert r.status_code in (400, 404)  # 404 if stage already closed


# ── Authorization ─────────────────────────────────────────────────────────────

class TestApprovalAuthorization:
    def test_non_member_cannot_approve(self, client, db):
        admin = make_user(db, email="nma@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="nmap@x.com", role=models.UserRole.approver)
        outsider = make_user(db, email="nmo@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="nms@x.com")
        group = make_group(db, members=[approver])  # outsider NOT in group
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()

        r = approve(client, req.id, outsider.id)
        assert r.status_code == 403

    def test_admin_can_always_approve(self, client, db):
        admin = make_user(db, email="aca2@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="aca2ap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="aca2s@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()

        r = approve(client, req.id, admin.id)
        assert r.status_code == 200

    def test_approve_nonexistent_request(self, client, db):
        approver = make_user(db, email="anr@x.com", role=models.UserRole.approver)
        db.commit()
        r = approve(client, 99999, approver.id)
        assert r.status_code == 404

    def test_approve_already_resolved_request(self, client, db):
        admin = make_user(db, email="aar@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="aarp@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="aars@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        req.status = models.RequestStatus.approved
        db.commit()

        r = approve(client, req.id, approver.id)
        assert r.status_code == 400

    def test_unknown_user_cannot_approve(self, client, db):
        admin = make_user(db, email="uuca@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="uucas@x.com")
        group = make_group(db)
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()
        r = approve(client, req.id, 99999)
        assert r.status_code == 404

    def test_missing_stage_def_returns_400(self, client, db):
        """Regression: stage_id points to deleted WorkflowStage — must be 400 not 500."""
        admin = make_user(db, email="msd@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="msdap@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="msds@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        # Delete the underlying WorkflowStage to simulate orphan
        stage = wf.stages[0]
        db.delete(stage)
        db.flush()
        db.commit()

        r = approve(client, req.id, approver.id)
        assert r.status_code == 400


# ── Delegation ────────────────────────────────────────────────────────────────

class TestDelegation:
    def test_delegate_decision(self, client, db):
        admin = make_user(db, email="dda@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="ddap@x.com", role=models.UserRole.approver)
        delegate = make_user(db, email="ddd@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="dds@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()

        r = client.post(f"/api/approvals/?user_id={approver.id}", json={
            "request_id": req.id,
            "decision": "delegated",
            "delegated_to_id": delegate.id,
        })
        assert r.status_code == 200


# ── Pending Approvals ─────────────────────────────────────────────────────────

class TestPendingApprovals:
    def test_pending_returns_request_ids(self, client, db):
        admin = make_user(db, email="pap@x.com", role=models.UserRole.admin)
        approver = make_user(db, email="papp@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="paps@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()

        r = client.get(f"/api/approvals/pending?user_id={approver.id}")
        assert r.status_code == 200
        assert req.id in r.json()

    def test_admin_sees_all_pending(self, client, db):
        admin = make_user(db, email="asp@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="asps@x.com")
        approver = make_user(db, email="aspa@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        db.commit()

        r = client.get(f"/api/approvals/pending?user_id={admin.id}")
        assert r.status_code == 200
        assert req.id in r.json()

    def test_pending_empty_for_non_member(self, client, db):
        admin = make_user(db, email="penm@x.com", role=models.UserRole.admin)
        outsider = make_user(db, email="peno@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="pens@x.com")
        approver = make_user(db, email="penap@x.com", role=models.UserRole.approver)
        group = make_group(db, members=[approver])  # outsider NOT in group
        wf = make_workflow(db, admin, group)
        make_request(db, submitter, wf)
        db.commit()

        r = client.get(f"/api/approvals/pending?user_id={outsider.id}")
        assert r.status_code == 200
        assert r.json() == []


# ── OOO live stand-in approval flow ────────────────────────────────────────────────

class TestOOOLiveStandIn:
    """
    POST /api/approvals/ when the acting user is not themselves a stage
    member but IS the live delegate of a currently-OOO member who is. The
    action is recorded with approver_id=delegate's own id (see take_action's
    `action = models.ApprovalAction(approver_id=current_user.id, ...)`), but
    the duplicate-prevention check and voting math key off the *principal's*
    slot (approver_id local var = acting_for_id or current_user.id) before
    the row is written. We assert on both: the actual stored approver_id,
    and that the stage completes as if the principal acted.
    """

    def _setup(self, db, ooo_future=True, has_delegate=True, voting_rule=models.VotingRule.any):
        from datetime import datetime, timedelta
        admin = make_user(db, email="ooa@x.com", role=models.UserRole.admin)
        principal = make_user(db, email="ooop@x.com", role=models.UserRole.approver)
        delegate = make_user(db, email="oood@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="ooos@x.com")
        group = make_group(db, members=[principal])
        wf = make_workflow(db, admin, group, voting_rule=voting_rule)
        req = make_request(db, submitter, wf)
        if ooo_future:
            principal.ooo_until = datetime.utcnow() + timedelta(days=2)
        if has_delegate:
            principal.delegate_id = delegate.id
        db.commit()
        return req, principal, delegate, submitter

    def test_delegate_can_act_for_ooo_principal(self, client, db):
        req, principal, delegate, _ = self._setup(db)
        r = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        db.refresh(req)
        # voting_rule=any, principal is the only required member -> stage
        # completes as soon as the delegate's stand-in action lands.
        assert req.status == models.RequestStatus.approved

    def test_action_recorded_against_delegates_own_user_id(self, client, db):
        """As implemented, the ApprovalAction row's approver_id is the acting
        delegate's own id, not the principal's — only the duplicate-check /
        voting-math local variable resolves to the principal's slot."""
        req, principal, delegate, _ = self._setup(db)
        r = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        action_id = r.json()["id"]
        action = db.query(models.ApprovalAction).filter(models.ApprovalAction.id == action_id).first()
        assert action.approver_id == delegate.id

    def test_activity_log_notes_acting_on_behalf_of(self, client, db):
        req, principal, delegate, _ = self._setup(db)
        client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        db.refresh(req)
        logs = [a.detail for a in req.activity_log if a.detail]
        assert any("on behalf of" in d and principal.name in d for d in logs)

    def test_duplicate_stand_in_action_blocked(self, client, db):
        """A second stand-in action for the same OOO principal's slot is
        blocked as a duplicate, even though it's coming from the delegate
        (the duplicate check keys off the principal's slot)."""
        req, principal, delegate, _ = self._setup(db, voting_rule=models.VotingRule.all)
        # Add a second required member so the stage doesn't complete on the
        # first action, letting us attempt a second stand-in action.
        other = make_user(db, email="oooother@x.com", role=models.UserRole.approver)
        db.add(models.ApproverGroupMember(
            group_id=req.workflow.stages[0].approver_group_id,
            user_id=other.id, sequential_order=1,
        ))
        db.commit()

        r1 = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r1.status_code == 200

        r2 = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r2.status_code == 400

    def test_non_delegate_outsider_still_blocked(self, client, db):
        """An OOO principal existing elsewhere in the group doesn't open the
        door for just anyone — only that principal's own designated delegate."""
        req, principal, delegate, _ = self._setup(db)
        outsider = make_user(db, email="oooout@x.com", role=models.UserRole.approver)
        db.commit()
        r = client.post(f"/api/approvals/?user_id={outsider.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 403

    def test_delegate_blocked_when_principal_not_ooo(self, client, db):
        """If ooo_until is unset (or in the past), the delegate has no
        standing to act — they're just another non-member."""
        req, principal, delegate, _ = self._setup(db, ooo_future=False)
        r = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 403

    def test_delegate_blocked_when_ooo_in_past(self, client, db):
        from datetime import datetime, timedelta
        req, principal, delegate, _ = self._setup(db, ooo_future=False)
        principal.ooo_until = datetime.utcnow() - timedelta(days=1)
        db.commit()
        r = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 403

    def test_member_without_delegate_blocks_non_member_caller(self, client, db):
        """OOO with no delegate_id set: nobody gets stand-in access."""
        req, principal, delegate, _ = self._setup(db, has_delegate=False)
        r = client.post(f"/api/approvals/?user_id={delegate.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 403

    def test_admin_unaffected_by_ooo_logic(self, client, db):
        """Admins bypass the membership/OOO check entirely regardless of
        anyone else's OOO status."""
        req, principal, delegate, _ = self._setup(db)
        admin2 = make_user(db, email="ooadmin2@x.com", role=models.UserRole.admin)
        db.commit()
        r = client.post(f"/api/approvals/?user_id={admin2.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200


# ── Optional group members: excluded from voting math ───────────────────────────

class TestOptionalMemberVoting:
    """
    Workflow #4/#11 — an ApproverGroupMember with is_optional=True may still
    act (their decision is recorded for the audit trail), but it never
    counts toward stage completion (approval/rejection) per
    _check_stage_completion's optional_member_ids exclusion.
    """

    def _setup(self, db, voting_rule=models.VotingRule.all,
               rejection_behavior=models.RejectionBehavior.stop):
        required = make_user(db, email="omvreq@x.com", role=models.UserRole.approver)
        optional = make_user(db, email="omvopt@x.com", role=models.UserRole.approver)
        admin = make_user(db, email="omvadmin@x.com", role=models.UserRole.admin)
        submitter = make_user(db, email="omvsub@x.com")
        group = make_group(db, members=[required, optional])
        # Mark `optional` as is_optional=True on the live membership row;
        # make_request builds no workflow_snapshot, so get_stage_config
        # falls back to the live tables and picks this up.
        opt_member = db.query(models.ApproverGroupMember).filter(
            models.ApproverGroupMember.group_id == group.id,
            models.ApproverGroupMember.user_id == optional.id,
        ).first()
        opt_member.is_optional = True
        wf = make_workflow(db, admin, group, voting_rule=voting_rule,
                            rejection_behavior=rejection_behavior)
        req = make_request(db, submitter, wf)
        db.commit()
        return req, required, optional

    def test_optional_member_alone_cannot_complete_any_rule_stage(self, client, db):
        """voting_rule=any normally completes on a single approval — but an
        optional member's approval must NOT trigger that."""
        req, required, optional = self._setup(db, voting_rule=models.VotingRule.any)
        r = client.post(f"/api/approvals/?user_id={optional.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.pending

    def test_required_member_alone_completes_any_rule_stage(self, client, db):
        req, required, optional = self._setup(db, voting_rule=models.VotingRule.any)
        r = client.post(f"/api/approvals/?user_id={required.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

    def test_all_rule_completes_on_required_member_only(self, client, db):
        """voting_rule=all should only require the *required* members —
        group_members count excludes the optional one."""
        req, required, optional = self._setup(db, voting_rule=models.VotingRule.all)
        r = client.post(f"/api/approvals/?user_id={required.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

    def test_optional_members_rejection_does_not_block_stage(self, client, db):
        """An optional member's rejection is recorded but must not trigger
        the rejection_behavior — the stage proceeds on the required member's
        approval alone."""
        req, required, optional = self._setup(
            db, voting_rule=models.VotingRule.all,
            rejection_behavior=models.RejectionBehavior.stop,
        )
        r1 = client.post(f"/api/approvals/?user_id={optional.id}", json={
            "request_id": req.id, "decision": "rejected",
        })
        assert r1.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.pending  # not rejected

        r2 = client.post(f"/api/approvals/?user_id={required.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r2.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

    def test_optional_members_action_is_recorded_for_audit(self, client, db):
        """The optional member's action still writes an ApprovalAction row
        and ActivityLog entry — it's excluded from voting math, not from
        the audit trail."""
        req, required, optional = self._setup(db, voting_rule=models.VotingRule.any)
        client.post(f"/api/approvals/?user_id={optional.id}", json={
            "request_id": req.id, "decision": "approved", "comment": "FYI only",
        })
        action = db.query(models.ApprovalAction).filter(
            models.ApprovalAction.approver_id == optional.id
        ).first()
        assert action is not None
        assert action.comment == "FYI only"

    def test_optional_member_can_still_act_only_once(self, client, db):
        req, required, optional = self._setup(db, voting_rule=models.VotingRule.all)
        r1 = client.post(f"/api/approvals/?user_id={optional.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r1.status_code == 200
        r2 = client.post(f"/api/approvals/?user_id={optional.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r2.status_code == 400


# ── Optional stages: auto-skip when the group has no members ──────────────────

class TestOptionalStageAutoSkip:
    """
    _advance_request: a WorkflowStage with is_optional=True whose approver
    group has zero members is auto-approved/skipped rather than left
    blocking the request indefinitely — distinct from TestOptionalMemberVoting
    above, which is about an optional *member* inside a populated group.
    """

    def _build_two_stage_request(self, db, *, stage2_optional, stage2_has_members):
        from datetime import datetime, timedelta
        admin = make_user(db, email="osaadmin@x.com", role=models.UserRole.admin)
        s1_approver = make_user(db, email="osas1@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email="osasub@x.com")
        group1 = make_group(db, name="OSA Group 1", members=[s1_approver])
        group2 = make_group(db, name="OSA Group 2", members=[])
        if stage2_has_members:
            s2_approver = make_user(db, email="osas2@x.com", role=models.UserRole.approver)
            db.add(models.ApproverGroupMember(group_id=group2.id, user_id=s2_approver.id, sequential_order=0))
            db.flush()
        else:
            s2_approver = None

        wf = models.Workflow(
            name="OSA Workflow", type=models.WorkflowType.approval,
            escalation_hours=24, rejection_behavior=models.RejectionBehavior.stop,
            notification_channel=models.NotificationChannel.email,
            created_by_id=admin.id,
        )
        db.add(wf)
        db.flush()
        stage1 = models.WorkflowStage(
            workflow_id=wf.id, name="Stage 1", type=models.WorkflowType.approval,
            order=1, approver_group_id=group1.id, sla_hours=48,
            voting_rule=models.VotingRule.any, is_optional=False,
        )
        stage2 = models.WorkflowStage(
            workflow_id=wf.id, name="Stage 2 (optional)", type=models.WorkflowType.approval,
            order=2, approver_group_id=group2.id, sla_hours=48,
            voting_rule=models.VotingRule.any, is_optional=stage2_optional,
        )
        db.add_all([stage1, stage2])
        db.flush()

        req = models.WorkflowRequest(
            title="OSA Request", workflow_id=wf.id, submitter_id=submitter.id,
            status=models.RequestStatus.pending, current_stage=1,
        )
        db.add(req)
        db.flush()
        now = datetime.utcnow()
        rs1 = models.RequestStage(
            request_id=req.id, stage_id=stage1.id, stage_order=1,
            status=models.RequestStatus.pending, started_at=now,
            sla_deadline=now + timedelta(hours=48),
        )
        rs2 = models.RequestStage(
            request_id=req.id, stage_id=stage2.id, stage_order=2,
            status=models.RequestStatus.pending,
        )
        db.add_all([rs1, rs2])
        db.flush()
        db.commit()
        return req, s1_approver, s2_approver

    def test_optional_stage_with_no_members_is_skipped(self, client, db):
        req, s1_approver, _ = self._build_two_stage_request(
            db, stage2_optional=True, stage2_has_members=False,
        )
        r = client.post(f"/api/approvals/?user_id={s1_approver.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        db.refresh(req)
        # Stage 2 had no members and was optional -> auto-skipped -> request
        # fully resolves off the back of stage 1 alone.
        assert req.status == models.RequestStatus.approved

        stage2 = next(rs for rs in req.stages if rs.stage_order == 2)
        assert stage2.status == models.RequestStatus.approved
        logs = [a.action for a in req.activity_log]
        assert "skipped" in logs

    def test_required_stage_with_no_members_blocks_instead_of_skipping(self, client, db):
        """Sanity check on the inverse: a NON-optional empty-group stage is
        NOT auto-skipped — it's started and just has nobody to notify, so
        the request stays pending at that stage."""
        req, s1_approver, _ = self._build_two_stage_request(
            db, stage2_optional=False, stage2_has_members=False,
        )
        r = client.post(f"/api/approvals/?user_id={s1_approver.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.pending
        assert req.current_stage == 2

    def test_optional_stage_with_members_is_not_skipped(self, client, db):
        """An optional stage that DOES have approver-group members behaves
        like a normal stage — it still has to be acted on (optionality here
        is about the stage being skippable when empty, not about requiring
        zero input when populated)."""
        req, s1_approver, s2_approver = self._build_two_stage_request(
            db, stage2_optional=True, stage2_has_members=True,
        )
        r = client.post(f"/api/approvals/?user_id={s1_approver.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.pending
        assert req.current_stage == 2

        r2 = client.post(f"/api/approvals/?user_id={s2_approver.id}", json={
            "request_id": req.id, "decision": "approved",
        })
        assert r2.status_code == 200
        db.refresh(req)
        assert req.status == models.RequestStatus.approved

