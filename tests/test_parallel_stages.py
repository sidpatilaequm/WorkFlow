"""
tests/test_parallel_stages.py — Workflow #1: parallel stage execution

WorkflowStage.parallel_group: stages in the same workflow sharing a non-null
integer start simultaneously when a request is submitted (or when a prior
serial stage advances past them). All stages in the group must reach an
approved/skipped state before the workflow advances to the next serial stage.

Architecture under test:
  routers/requests.py:submit_request        — starts all stages in group
  routers/approvals.py:_advance_request     — checks group completion before advancing
  routers/approvals.py:_check_stage_completion — sets current_stage, calls _advance_request
  routers/approvals.py:take_action          — parallel-aware active-stage lookup

Test topologies used:
  A)  [PG=1: S1, S2]                    — two parallel stages, no serial stages
  B)  S0 → [PG=1: S1, S2] → S3         — serial then parallel then serial
  C)  [PG=1: S1, S2] → [PG=2: S3, S4]  — two back-to-back parallel groups
"""
import uuid
from datetime import datetime, timedelta

import models
import pytest
from conftest import make_user, make_group


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _approve(client, req_id, user_id):
    r = client.post(f"/api/approvals/?user_id={user_id}", json={
        "request_id": req_id, "decision": "approved",
    })
    return r


def _reject(client, req_id, user_id):
    r = client.post(f"/api/approvals/?user_id={user_id}", json={
        "request_id": req_id, "decision": "rejected",
    })
    return r


def _build_workflow(db, admin, stages_spec):
    """
    Create a Workflow with arbitrary stage topology.

    stages_spec: list of dicts, each:
      {
        "order": int,
        "group": ApproverGroup,
        "parallel_group": int | None,   # default None
        "voting_rule": VotingRule,      # default any
        "is_optional": bool,            # default False
      }
    """
    wf = models.Workflow(
        name=f"PG-WF-{_uid()}",
        type=models.WorkflowType.approval,
        escalation_hours=24,
        rejection_behavior=models.RejectionBehavior.stop,
        notification_channel=models.NotificationChannel.email,
        created_by_id=admin.id,
    )
    db.add(wf)
    db.flush()
    for spec in stages_spec:
        stage = models.WorkflowStage(
            workflow_id=wf.id,
            name=f"Stage {spec['order']}",
            type=models.WorkflowType.approval,
            order=spec["order"],
            approver_group_id=spec["group"].id,
            sla_hours=48,
            voting_rule=spec.get("voting_rule", models.VotingRule.any),
            parallel_group=spec.get("parallel_group"),
            is_optional=spec.get("is_optional", False),
        )
        db.add(stage)
    db.flush()
    return wf


def _submit(client, submitter, wf):
    """POST /api/requests/ and return the parsed response dict."""
    r = client.post(
        f"/api/requests/?user_id={submitter.id}",
        json={"title": f"Req-{_uid()}", "workflow_id": wf.id},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ─── Topology A: two parallel stages, no serial stages ────────────────────────

class TestTopologyA:
    """[PG=1: S1, S2]"""

    def _setup(self, db):
        admin = make_user(db, email=f"pga-adm-{_uid()}@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email=f"pga-a1-{_uid()}@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email=f"pga-a2-{_uid()}@x.com", role=models.UserRole.approver)
        sub = make_user(db, email=f"pga-sub-{_uid()}@x.com")
        g1 = make_group(db, name=f"G1-{_uid()}", members=[a1])
        g2 = make_group(db, name=f"G2-{_uid()}", members=[a2])
        wf = _build_workflow(db, admin, [
            {"order": 1, "group": g1, "parallel_group": 1},
            {"order": 2, "group": g2, "parallel_group": 1},
        ])
        db.commit()
        return wf, sub, a1, a2

    def test_both_stages_start_on_submit(self, client, db):
        wf, sub, a1, a2 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        started = [rs for rs in req.stages if rs.started_at is not None]
        assert len(started) == 2

    def test_first_approval_leaves_request_pending(self, client, db):
        wf, sub, a1, a2 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        r = _approve(client, req_id, a1.id)
        assert r.status_code == 200

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.pending

    def test_second_approval_completes_request(self, client, db):
        wf, sub, a1, a2 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _approve(client, req_id, a1.id)
        r = _approve(client, req_id, a2.id)
        assert r.status_code == 200

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.approved

    def test_both_approvers_can_act_in_any_order(self, client, db):
        """a2 acting before a1 must also work — parallel means no ordering."""
        wf, sub, a1, a2 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _approve(client, req_id, a2.id)
        _approve(client, req_id, a1.id)

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.approved

    def test_rejection_in_parallel_group_stops_workflow(self, client, db):
        """A rejection in any parallel stage triggers the workflow's
        rejection_behavior (stop) — even if the sibling stage is still pending."""
        wf, sub, a1, a2 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        r = _reject(client, req_id, a1.id)
        assert r.status_code == 200

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.rejected

    def test_duplicate_action_blocked(self, client, db):
        wf, sub, a1, a2 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _approve(client, req_id, a1.id)
        r = _approve(client, req_id, a1.id)   # second time
        assert r.status_code == 400

    def test_outsider_cannot_act_on_parallel_stage(self, client, db):
        wf, sub, a1, a2 = self._setup(db)
        outsider = make_user(db, email=f"pga-out-{_uid()}@x.com", role=models.UserRole.approver)
        db.commit()
        data = _submit(client, sub, wf)
        req_id = data["id"]

        r = _approve(client, req_id, outsider.id)
        assert r.status_code == 403


# ─── Topology B: serial → parallel → serial ───────────────────────────────────

class TestTopologyB:
    """S0(serial) → [PG=1: S1, S2](parallel) → S3(serial)"""

    def _setup(self, db):
        admin = make_user(db, email=f"pgb-adm-{_uid()}@x.com", role=models.UserRole.admin)
        a0 = make_user(db, email=f"pgb-a0-{_uid()}@x.com", role=models.UserRole.approver)
        a1 = make_user(db, email=f"pgb-a1-{_uid()}@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email=f"pgb-a2-{_uid()}@x.com", role=models.UserRole.approver)
        a3 = make_user(db, email=f"pgb-a3-{_uid()}@x.com", role=models.UserRole.approver)
        sub = make_user(db, email=f"pgb-sub-{_uid()}@x.com")
        g0 = make_group(db, name=f"G0-{_uid()}", members=[a0])
        g1 = make_group(db, name=f"G1-{_uid()}", members=[a1])
        g2 = make_group(db, name=f"G2-{_uid()}", members=[a2])
        g3 = make_group(db, name=f"G3-{_uid()}", members=[a3])
        wf = _build_workflow(db, admin, [
            {"order": 1, "group": g0, "parallel_group": None},  # serial stage 0
            {"order": 2, "group": g1, "parallel_group": 1},     # parallel group 1
            {"order": 3, "group": g2, "parallel_group": 1},     # parallel group 1
            {"order": 4, "group": g3, "parallel_group": None},  # serial stage 3
        ])
        db.commit()
        return wf, sub, a0, a1, a2, a3

    def test_only_serial_stage_starts_on_submit(self, client, db):
        wf, sub, a0, a1, a2, a3 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        started = [rs for rs in req.stages if rs.started_at is not None]
        assert len(started) == 1
        assert started[0].stage_order == 1

    def test_parallel_group_starts_after_serial_completes(self, client, db):
        wf, sub, a0, a1, a2, a3 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _approve(client, req_id, a0.id)

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.pending
        started = [rs for rs in req.stages if rs.started_at is not None]
        started_orders = {rs.stage_order for rs in started}
        # S0 done + S1 and S2 started
        assert {2, 3}.issubset(started_orders)

    def test_final_serial_stage_starts_after_full_parallel_group(self, client, db):
        wf, sub, a0, a1, a2, a3 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _approve(client, req_id, a0.id)   # serial stage done
        _approve(client, req_id, a1.id)   # parallel stage 2 done
        # Stage 4 should NOT have started yet
        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        s4 = next(rs for rs in req.stages if rs.stage_order == 4)
        assert s4.started_at is None
        assert req.status == models.RequestStatus.pending

        _approve(client, req_id, a2.id)   # parallel stage 3 done -> group complete
        db.refresh(req)
        s4 = next(rs for rs in req.stages if rs.stage_order == 4)
        assert s4.started_at is not None

    def test_full_chain_approves_request(self, client, db):
        wf, sub, a0, a1, a2, a3 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _approve(client, req_id, a0.id)
        _approve(client, req_id, a1.id)
        _approve(client, req_id, a2.id)
        _approve(client, req_id, a3.id)

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.approved

    def test_rejection_in_serial_stage_stops_before_parallel(self, client, db):
        wf, sub, a0, a1, a2, a3 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _reject(client, req_id, a0.id)

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.rejected
        # Parallel stages must never have started
        s2 = next(rs for rs in req.stages if rs.stage_order == 2)
        s3 = next(rs for rs in req.stages if rs.stage_order == 3)
        assert s2.started_at is None
        assert s3.started_at is None


# ─── Topology C: two back-to-back parallel groups ─────────────────────────────

class TestTopologyC:
    """[PG=1: S1, S2] → [PG=2: S3, S4]"""

    def _setup(self, db):
        admin = make_user(db, email=f"pgc-adm-{_uid()}@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email=f"pgc-a1-{_uid()}@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email=f"pgc-a2-{_uid()}@x.com", role=models.UserRole.approver)
        a3 = make_user(db, email=f"pgc-a3-{_uid()}@x.com", role=models.UserRole.approver)
        a4 = make_user(db, email=f"pgc-a4-{_uid()}@x.com", role=models.UserRole.approver)
        sub = make_user(db, email=f"pgc-sub-{_uid()}@x.com")
        g1 = make_group(db, name=f"G1-{_uid()}", members=[a1])
        g2 = make_group(db, name=f"G2-{_uid()}", members=[a2])
        g3 = make_group(db, name=f"G3-{_uid()}", members=[a3])
        g4 = make_group(db, name=f"G4-{_uid()}", members=[a4])
        wf = _build_workflow(db, admin, [
            {"order": 1, "group": g1, "parallel_group": 1},
            {"order": 2, "group": g2, "parallel_group": 1},
            {"order": 3, "group": g3, "parallel_group": 2},
            {"order": 4, "group": g4, "parallel_group": 2},
        ])
        db.commit()
        return wf, sub, a1, a2, a3, a4

    def test_first_group_starts_on_submit(self, client, db):
        wf, sub, a1, a2, a3, a4 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        started_orders = {rs.stage_order for rs in req.stages if rs.started_at is not None}
        assert started_orders == {1, 2}

    def test_second_group_starts_after_first_group_completes(self, client, db):
        wf, sub, a1, a2, a3, a4 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        _approve(client, req_id, a1.id)
        # Only one of the first group done — second group must not start
        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        s3 = next(rs for rs in req.stages if rs.stage_order == 3)
        assert s3.started_at is None

        _approve(client, req_id, a2.id)
        db.refresh(req)
        started_orders = {rs.stage_order for rs in req.stages if rs.started_at is not None}
        assert {3, 4}.issubset(started_orders)

    def test_full_two_group_chain_approves_request(self, client, db):
        wf, sub, a1, a2, a3, a4 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        for approver in (a1, a2, a3, a4):
            _approve(client, req_id, approver.id)

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.approved

    def test_all_four_stage_orders_recorded(self, client, db):
        """Every stage must end up as approved in the activity log."""
        wf, sub, a1, a2, a3, a4 = self._setup(db)
        data = _submit(client, sub, wf)
        req_id = data["id"]

        for approver in (a1, a2, a3, a4):
            _approve(client, req_id, approver.id)

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        db.refresh(req)
        assert all(rs.status == models.RequestStatus.approved for rs in req.stages)


# ─── Edge cases ───────────────────────────────────────────────────────────────

class TestParallelEdgeCases:

    def test_single_stage_parallel_group_behaves_like_serial(self, client, db):
        """A parallel_group with only one stage should work exactly like a
        single serial stage — it starts on submit and completes immediately
        when that one approver acts."""
        admin = make_user(db, email=f"pg-single-adm-{_uid()}@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email=f"pg-single-a1-{_uid()}@x.com", role=models.UserRole.approver)
        sub = make_user(db, email=f"pg-single-sub-{_uid()}@x.com")
        g1 = make_group(db, name=f"Gs-{_uid()}", members=[a1])
        wf = _build_workflow(db, admin, [
            {"order": 1, "group": g1, "parallel_group": 1},
        ])
        db.commit()

        data = _submit(client, sub, wf)
        req_id = data["id"]
        _approve(client, req_id, a1.id)

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.approved

    def test_workflow_snapshot_captures_parallel_group(self, client, db):
        """The frozen workflow_snapshot must include parallel_group for each
        stage so get_stage_config can serve it to _advance_request after
        a live workflow edit."""
        admin = make_user(db, email=f"pg-snap-adm-{_uid()}@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email=f"pg-snap-a1-{_uid()}@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email=f"pg-snap-a2-{_uid()}@x.com", role=models.UserRole.approver)
        sub = make_user(db, email=f"pg-snap-sub-{_uid()}@x.com")
        g1 = make_group(db, name=f"Gsnap1-{_uid()}", members=[a1])
        g2 = make_group(db, name=f"Gsnap2-{_uid()}", members=[a2])
        wf = _build_workflow(db, admin, [
            {"order": 1, "group": g1, "parallel_group": 7},
            {"order": 2, "group": g2, "parallel_group": 7},
        ])
        db.commit()

        data = _submit(client, sub, wf)
        req = db.query(models.WorkflowRequest).filter(
            models.WorkflowRequest.id == data["id"]
        ).first()

        snap = req.workflow_snapshot
        assert snap is not None
        for stage_snap in snap["stages"]:
            assert stage_snap.get("parallel_group") == 7

    def test_admin_can_act_on_any_parallel_stage(self, client, db):
        """Admins bypass membership checks — they should be able to approve
        any running parallel stage regardless of which group they belong to."""
        admin = make_user(db, email=f"pg-adm-act-{_uid()}@x.com", role=models.UserRole.admin)
        a2 = make_user(db, email=f"pg-adm-a2-{_uid()}@x.com", role=models.UserRole.approver)
        sub = make_user(db, email=f"pg-adm-sub-{_uid()}@x.com")
        g1 = make_group(db, name=f"Gadm1-{_uid()}", members=[])   # no one except admin
        g2 = make_group(db, name=f"Gadm2-{_uid()}", members=[a2])
        wf = _build_workflow(db, admin, [
            {"order": 1, "group": g1, "parallel_group": 1},
            {"order": 2, "group": g2, "parallel_group": 1},
        ])
        db.commit()

        data = _submit(client, sub, wf)
        req_id = data["id"]

        r = _approve(client, req_id, admin.id)   # admin approves stage 1
        assert r.status_code == 200
        _approve(client, req_id, a2.id)          # a2 approves stage 2

        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
        assert req.status == models.RequestStatus.approved
