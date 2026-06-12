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
