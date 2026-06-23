"""
tests/test_escalation.py — services/escalation.py scheduler jobs:
    1. run_escalation_check     — SLA breach -> escalated status
    2. send_pending_reminders   — snapshot-based per-stage reminders
    3. send_message_reminders   — ScheduledMessage re-sends (Messaging #2)

SMTP isn't configured in the test environment, so notification_service.send_email
just logs a warning and returns False — that's fine, these tests assert on DB
side-effects (ActivityLog rows, timestamps, counters), not on actual delivery.

Each job is called with the test's own `db` fixture session injected
(escalation.<job>(db)) rather than letting it open its own SessionLocal().
The jobs default to opening their own session/transaction in production
(db=None), but that would commit against the *same* underlying SQLite
connection the `db` fixture already has an open transaction on — committing
there ends the fixture's transaction early, which breaks the per-test
rollback-based isolation conftest.py relies on. Injecting `db` keeps
everything inside the one transaction the fixture manages.
"""
import uuid
from datetime import datetime, timedelta

import models
from conftest import make_user, make_group, make_workflow, make_request
import services.escalation as escalation


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ── Job 1: SLA breach escalation ───────────────────────────────────────────────

class TestRunEscalationCheck:
    def test_overdue_stage_escalates_request(self, db):
        admin = make_user(db, email=f"esc-admin-{_uid()}@x.com", role=models.UserRole.admin)
        approver = make_user(db, email=f"esc-appr-{_uid()}@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email=f"esc-sub-{_uid()}@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)

        rs = req.stages[0]
        rs.sla_deadline = datetime.utcnow() - timedelta(hours=1)
        db.commit()

        escalation.run_escalation_check(db)

        db.refresh(req)
        db.refresh(rs)
        assert rs.is_sla_breached is True
        assert req.status == models.RequestStatus.escalated
        logs = [a.action for a in req.activity_log]
        assert "escalated" in logs

    def test_stage_within_sla_is_untouched(self, db):
        admin = make_user(db, email=f"esc-admin2-{_uid()}@x.com", role=models.UserRole.admin)
        approver = make_user(db, email=f"esc-appr2-{_uid()}@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email=f"esc-sub2-{_uid()}@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)  # default sla_deadline = now + 48h

        db.commit()
        escalation.run_escalation_check(db)

        db.refresh(req)
        assert req.status == models.RequestStatus.pending


# ── Job 2: snapshot-based pending reminders ────────────────────────────────────

class TestSendPendingReminders:
    def _setup(self, db, reminder_after_hours=1, reminder_interval_hours=None,
               stage_started_hours_ago=2, last_reminded_at=None):
        admin = make_user(db, email=f"pr-admin-{_uid()}@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email=f"pr-a1-{_uid()}@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email=f"pr-a2-{_uid()}@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email=f"pr-sub-{_uid()}@x.com")
        group = make_group(db, members=[a1, a2])
        wf = make_workflow(db, admin, group)
        wf.reminder_after_hours = reminder_after_hours
        wf.reminder_interval_hours = reminder_interval_hours
        req = make_request(db, submitter, wf)
        rs = req.stages[0]
        rs.started_at = datetime.utcnow() - timedelta(hours=stage_started_hours_ago)
        rs.last_reminded_at = last_reminded_at
        db.commit()
        return req, rs, a1, a2

    def test_first_reminder_fires_after_reminder_after_hours(self, db):
        req, rs, a1, a2 = self._setup(db, reminder_after_hours=1, stage_started_hours_ago=2)

        escalation.send_pending_reminders(db)

        db.refresh(rs)
        assert rs.last_reminded_at is not None
        reminded_user_ids = {
            a.user_id for a in req.activity_log if a.action == "reminder_sent"
        }
        assert reminded_user_ids == {a1.id, a2.id}

    def test_no_reminder_before_reminder_after_hours(self, db):
        req, rs, a1, a2 = self._setup(db, reminder_after_hours=24, stage_started_hours_ago=2)

        escalation.send_pending_reminders(db)

        db.refresh(rs)
        assert rs.last_reminded_at is None

    def test_already_acted_approver_excluded(self, db):
        req, rs, a1, a2 = self._setup(db, reminder_after_hours=1, stage_started_hours_ago=2)
        db.add(models.ApprovalAction(
            request_stage_id=rs.id, approver_id=a1.id,
            decision=models.ApprovalDecision.approved,
        ))
        db.commit()

        escalation.send_pending_reminders(db)

        reminded_user_ids = {
            a.user_id for a in req.activity_log if a.action == "reminder_sent"
        }
        assert reminded_user_ids == {a2.id}

    def test_no_repeat_reminder_before_interval_elapsed(self, db):
        req, rs, a1, a2 = self._setup(
            db, reminder_after_hours=1, reminder_interval_hours=4,
            stage_started_hours_ago=10,
            last_reminded_at=datetime.utcnow() - timedelta(hours=1),
        )

        escalation.send_pending_reminders(db)

        reminded = [a for a in req.activity_log if a.action == "reminder_sent"]
        assert reminded == []

    def test_repeat_reminder_after_interval_elapsed(self, db):
        req, rs, a1, a2 = self._setup(
            db, reminder_after_hours=1, reminder_interval_hours=4,
            stage_started_hours_ago=10,
            last_reminded_at=datetime.utcnow() - timedelta(hours=5),
        )

        escalation.send_pending_reminders(db)

        reminded_user_ids = {
            a.user_id for a in req.activity_log if a.action == "reminder_sent"
        }
        assert reminded_user_ids == {a1.id, a2.id}

    def test_workflow_without_reminder_settings_is_skipped(self, db):
        req, rs, a1, a2 = self._setup(db, reminder_after_hours=None, stage_started_hours_ago=10)

        escalation.send_pending_reminders(db)

        db.refresh(rs)
        assert rs.last_reminded_at is None

    def test_uses_snapshot_membership_not_live_group(self, db):
        """
        If the live group changes after submission, snapshot-based reminders
        should still go to the approvers frozen at submission time — this is
        the behavior workflow_snapshot.get_stage_config is meant to enforce.
        """
        admin = make_user(db, email=f"pr-snap-admin-{_uid()}@x.com", role=models.UserRole.admin)
        a1 = make_user(db, email=f"pr-snap-a1-{_uid()}@x.com", role=models.UserRole.approver)
        a2 = make_user(db, email=f"pr-snap-a2-{_uid()}@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email=f"pr-snap-sub-{_uid()}@x.com")
        group = make_group(db, members=[a1])
        wf = make_workflow(db, admin, group)
        wf.reminder_after_hours = 1
        req = make_request(db, submitter, wf)

        # Freeze a snapshot as if the request had been submitted with only a1
        from routers.requests import _build_workflow_snapshot
        req.workflow_snapshot = _build_workflow_snapshot(wf)

        rs = req.stages[0]
        rs.started_at = datetime.utcnow() - timedelta(hours=2)
        db.commit()

        # Now simulate an admin adding a2 to the *live* group after submission
        db.add(models.ApproverGroupMember(group_id=group.id, user_id=a2.id, sequential_order=1))
        db.commit()

        escalation.send_pending_reminders(db)

        reminded_user_ids = {
            a.user_id for a in req.activity_log if a.action == "reminder_sent"
        }
        # Only a1 (frozen at submission), not the later-added a2.
        assert reminded_user_ids == {a1.id}


# ── Job 3: scheduled ad-hoc message reminders ──────────────────────────────────

class TestSendMessageReminders:
    def _setup(self, db, to="submitter", reminder_interval_hours=1,
               max_reminders=None, reminders_sent=0, last_sent_hours_ago=2,
               request_status=models.RequestStatus.pending):
        admin = make_user(db, email=f"sm-admin-{_uid()}@x.com", role=models.UserRole.admin)
        approver = make_user(db, email=f"sm-appr-{_uid()}@x.com", role=models.UserRole.approver)
        submitter = make_user(db, email=f"sm-sub-{_uid()}@x.com")
        group = make_group(db, members=[approver])
        wf = make_workflow(db, admin, group)
        req = make_request(db, submitter, wf)
        req.status = request_status

        sm = models.ScheduledMessage(
            request_id=req.id,
            sender_id=admin.id,
            to=to,
            message="Still waiting on this — {{title}}",
            subject="Reminder: {{title}}",
            reminder_interval_hours=reminder_interval_hours,
            max_reminders=max_reminders,
            reminders_sent=reminders_sent,
            last_sent_at=datetime.utcnow() - timedelta(hours=last_sent_hours_ago),
            is_active=True,
        )
        db.add(sm)
        db.commit()
        return req, sm, submitter, approver

    def test_resends_when_interval_elapsed(self, db):
        req, sm, submitter, approver = self._setup(
            db, reminder_interval_hours=1, last_sent_hours_ago=2,
        )

        escalation.send_message_reminders(db)

        db.refresh(sm)
        assert sm.reminders_sent == 1
        assert sm.is_active is True
        logs = [a for a in req.activity_log if a.action == "scheduled_message_sent"]
        assert len(logs) == 1

    def test_no_resend_before_interval_elapsed(self, db):
        req, sm, submitter, approver = self._setup(
            db, reminder_interval_hours=4, last_sent_hours_ago=1,
        )

        escalation.send_message_reminders(db)

        db.refresh(sm)
        assert sm.reminders_sent == 0

    def test_deactivates_when_request_resolved(self, db):
        req, sm, submitter, approver = self._setup(
            db, reminder_interval_hours=1, last_sent_hours_ago=2,
            request_status=models.RequestStatus.approved,
        )

        escalation.send_message_reminders(db)

        db.refresh(sm)
        assert sm.is_active is False
        assert sm.reminders_sent == 0

    def test_deactivates_after_max_reminders_reached(self, db):
        req, sm, submitter, approver = self._setup(
            db, reminder_interval_hours=1, last_sent_hours_ago=2,
            max_reminders=2, reminders_sent=2,
        )

        escalation.send_message_reminders(db)

        db.refresh(sm)
        assert sm.is_active is False
        assert sm.reminders_sent == 2  # unchanged — it was already at the cap

    def test_resend_can_reach_then_hit_max_reminders(self, db):
        req, sm, submitter, approver = self._setup(
            db, reminder_interval_hours=1, last_sent_hours_ago=2,
            max_reminders=1, reminders_sent=0,
        )

        escalation.send_message_reminders(db)

        db.refresh(sm)
        assert sm.reminders_sent == 1
        assert sm.is_active is False  # hit the cap on this send

    def test_current_approvers_resolved_via_snapshot(self, db):
        """
        to="current_approvers" should resolve recipients through the frozen
        workflow_snapshot rather than the live group, consistent with job 2.
        """
        req, sm, submitter, approver = self._setup(
            db, to="current_approvers", reminder_interval_hours=1, last_sent_hours_ago=2,
        )
        from routers.requests import _build_workflow_snapshot
        wf = req.workflow
        req.workflow_snapshot = _build_workflow_snapshot(wf)
        db.commit()

        escalation.send_message_reminders(db)

        db.refresh(sm)
        assert sm.reminders_sent == 1

    def test_inactive_scheduled_message_is_ignored(self, db):
        req, sm, submitter, approver = self._setup(
            db, reminder_interval_hours=1, last_sent_hours_ago=2,
        )
        sm.is_active = False
        db.commit()

        escalation.send_message_reminders(db)

        db.refresh(sm)
        assert sm.reminders_sent == 0
