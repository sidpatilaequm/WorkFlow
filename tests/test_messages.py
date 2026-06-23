"""
tests/test_messages.py — routers/messages.py (Messaging #4)

POST   /api/messages/              — fire a standalone notification (no request context)
GET    /api/messages/              — list standalone messages
PATCH  /api/messages/{id}/deactivate — stop recurring resends

Also covers the Job-4 scheduler function:
  services.escalation.send_standalone_message_reminders
"""
import uuid
from datetime import datetime, timedelta

import models
import pytest
from auth_utils import create_access_token
from conftest import make_user
import services.escalation as escalation


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _token(user) -> str:
    return create_access_token(data={"sub": user.id})


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {_token(user)}"}


# ─── POST /api/messages/ ──────────────────────────────────────────────────────

class TestSendStandaloneMessage:

    def test_send_one_shot_message(self, client, db):
        sender = make_user(db, email=f"ssm-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={
                "to_emails": ["vendor@example.com"],
                "subject": "Hello",
                "message": "Please review the attached invoice.",
            },
            headers=_auth(sender),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["to_emails"] == ["vendor@example.com"]
        assert data["subject"] == "Hello"
        assert data["reminders_sent"] == 0
        # One-shot: no reminder_interval_hours → is_active should be False
        assert data["is_active"] is False

    def test_send_recurring_message_is_active(self, client, db):
        sender = make_user(db, email=f"srm-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={
                "to_emails": ["a@x.com"],
                "message": "Still waiting",
                "reminder_interval_hours": 4,
            },
            headers=_auth(sender),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["is_active"] is True
        assert data["reminder_interval_hours"] == 4

    def test_send_with_context_placeholder_rendering(self, client, db):
        """{{key}} placeholders in subject/message are rendered before send."""
        sender = make_user(db, email=f"ctx-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={
                "to_emails": ["buyer@x.com"],
                "subject": "Invoice from {{vendor}}",
                "message": "Amount due: {{amount}}",
                "context": {"vendor": "Infosys", "amount": "250000"},
            },
            headers=_auth(sender),
        )
        assert r.status_code == 201
        data = r.json()
        # Raw (un-rendered) template is stored on the record
        assert data["message"] == "Amount due: {{amount}}"
        assert data["context"]["vendor"] == "Infosys"

    def test_send_multiple_recipients(self, client, db):
        sender = make_user(db, email=f"mtr-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={
                "to_emails": ["a@x.com", "b@x.com", "c@x.com"],
                "message": "FYI",
            },
            headers=_auth(sender),
        )
        assert r.status_code == 201
        assert len(r.json()["to_emails"]) == 3

    def test_send_with_max_reminders(self, client, db):
        sender = make_user(db, email=f"smr-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={
                "to_emails": ["x@x.com"],
                "message": "Nudge",
                "reminder_interval_hours": 2,
                "max_reminders": 5,
            },
            headers=_auth(sender),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["max_reminders"] == 5
        assert data["is_active"] is True

    def test_empty_to_emails_rejected(self, client, db):
        sender = make_user(db, email=f"ete-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={"to_emails": [], "message": "Hi"},
            headers=_auth(sender),
        )
        assert r.status_code == 400

    def test_no_token_unauthorized(self, client):
        r = client.post(
            "/api/messages/",
            json={"to_emails": ["x@x.com"], "message": "Hi"},
        )
        assert r.status_code == 401

    def test_invalid_email_in_to_list_rejected(self, client, db):
        sender = make_user(db, email=f"inv-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={"to_emails": ["not-an-email"], "message": "Hi"},
            headers=_auth(sender),
        )
        assert r.status_code == 422

    def test_sender_id_recorded_on_message(self, client, db):
        sender = make_user(db, email=f"sid-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={"to_emails": ["x@x.com"], "message": "Hi"},
            headers=_auth(sender),
        )
        assert r.status_code == 201
        assert r.json()["sender_id"] == sender.id

    def test_message_persisted_in_db(self, client, db):
        sender = make_user(db, email=f"mpdb-{_uid()}@x.com")
        db.commit()
        r = client.post(
            "/api/messages/",
            json={"to_emails": ["x@x.com"], "message": "DB check"},
            headers=_auth(sender),
        )
        assert r.status_code == 201
        msg_id = r.json()["id"]
        row = db.query(models.StandaloneMessage).filter(
            models.StandaloneMessage.id == msg_id
        ).first()
        assert row is not None
        assert row.message == "DB check"
        assert row.sender_id == sender.id


# ─── GET /api/messages/ ───────────────────────────────────────────────────────

class TestListStandaloneMessages:

    def _send(self, client, sender, message="Hello", to=None, interval=None):
        payload = {"to_emails": to or ["x@x.com"], "message": message}
        if interval:
            payload["reminder_interval_hours"] = interval
        r = client.post("/api/messages/", json=payload, headers=_auth(sender))
        assert r.status_code == 201
        return r.json()

    def test_user_sees_own_messages(self, client, db):
        sender = make_user(db, email=f"lsm-{_uid()}@x.com")
        db.commit()
        self._send(client, sender, "Msg A")
        self._send(client, sender, "Msg B")
        r = client.get("/api/messages/", headers=_auth(sender))
        assert r.status_code == 200
        messages = r.json()
        assert len(messages) >= 2
        assert all(m["sender_id"] == sender.id for m in messages)

    def test_user_cannot_see_others_messages(self, client, db):
        s1 = make_user(db, email=f"lsm-s1-{_uid()}@x.com")
        s2 = make_user(db, email=f"lsm-s2-{_uid()}@x.com")
        db.commit()
        self._send(client, s1, "S1 message")
        r = client.get("/api/messages/", headers=_auth(s2))
        assert r.status_code == 200
        assert all(m["sender_id"] != s1.id for m in r.json())

    def test_admin_sees_all_messages(self, client, db):
        admin = make_user(db, email=f"lsm-adm-{_uid()}@x.com", role=models.UserRole.admin)
        s1 = make_user(db, email=f"lsm-adm-s1-{_uid()}@x.com")
        s2 = make_user(db, email=f"lsm-adm-s2-{_uid()}@x.com")
        db.commit()
        self._send(client, s1, "From s1")
        self._send(client, s2, "From s2")
        r = client.get("/api/messages/", headers=_auth(admin))
        assert r.status_code == 200
        sender_ids = {m["sender_id"] for m in r.json()}
        assert s1.id in sender_ids
        assert s2.id in sender_ids

    def test_active_only_filter(self, client, db):
        sender = make_user(db, email=f"aof-{_uid()}@x.com")
        db.commit()
        # recurring (is_active=True)
        self._send(client, sender, "Recurring", interval=2)
        # one-shot (is_active=False)
        self._send(client, sender, "One-shot")
        r = client.get("/api/messages/?active_only=true", headers=_auth(sender))
        assert r.status_code == 200
        assert all(m["is_active"] for m in r.json())

    def test_empty_list_for_new_user(self, client, db):
        sender = make_user(db, email=f"empty-{_uid()}@x.com")
        db.commit()
        r = client.get("/api/messages/", headers=_auth(sender))
        assert r.status_code == 200
        assert r.json() == []

    def test_no_token_unauthorized(self, client):
        r = client.get("/api/messages/")
        assert r.status_code == 401


# ─── PATCH /api/messages/{id}/deactivate ─────────────────────────────────────

class TestDeactivateStandaloneMessage:

    def _create_msg(self, db, sender, interval=4):
        msg = models.StandaloneMessage(
            sender_id=sender.id,
            to_emails=["x@x.com"],
            message="Recurring nudge",
            reminder_interval_hours=interval,
            reminders_sent=0,
            last_sent_at=datetime.utcnow(),
            is_active=True,
        )
        db.add(msg)
        db.flush()
        return msg

    def test_owner_can_deactivate(self, client, db):
        sender = make_user(db, email=f"deact-{_uid()}@x.com")
        db.commit()
        msg = self._create_msg(db, sender)
        db.commit()
        r = client.patch(
            f"/api/messages/{msg.id}/deactivate",
            headers=_auth(sender),
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is False

    def test_admin_can_deactivate_anyone(self, client, db):
        admin = make_user(db, email=f"deact-adm-{_uid()}@x.com", role=models.UserRole.admin)
        sender = make_user(db, email=f"deact-own-{_uid()}@x.com")
        db.commit()
        msg = self._create_msg(db, sender)
        db.commit()
        r = client.patch(
            f"/api/messages/{msg.id}/deactivate",
            headers=_auth(admin),
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is False

    def test_other_user_cannot_deactivate(self, client, db):
        sender = make_user(db, email=f"deact-s-{_uid()}@x.com")
        outsider = make_user(db, email=f"deact-o-{_uid()}@x.com")
        db.commit()
        msg = self._create_msg(db, sender)
        db.commit()
        r = client.patch(
            f"/api/messages/{msg.id}/deactivate",
            headers=_auth(outsider),
        )
        assert r.status_code == 403

    def test_nonexistent_message_404(self, client, db):
        user = make_user(db, email=f"deact-ne-{_uid()}@x.com")
        db.commit()
        r = client.patch(
            "/api/messages/99999/deactivate",
            headers=_auth(user),
        )
        assert r.status_code == 404

    def test_no_token_unauthorized(self, client, db):
        sender = make_user(db, email=f"deact-tok-{_uid()}@x.com")
        db.commit()
        msg = self._create_msg(db, sender)
        db.commit()
        r = client.patch(f"/api/messages/{msg.id}/deactivate")
        assert r.status_code == 401

    def test_deactivating_already_inactive_is_idempotent(self, client, db):
        """Deactivating an already-inactive message must not error."""
        sender = make_user(db, email=f"deact-idem-{_uid()}@x.com")
        db.commit()
        msg = self._create_msg(db, sender)
        msg.is_active = False
        db.commit()
        r = client.patch(
            f"/api/messages/{msg.id}/deactivate",
            headers=_auth(sender),
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is False


# ─── Job 4: send_standalone_message_reminders ─────────────────────────────────

class TestSendStandaloneMessageReminders:
    """
    services.escalation.send_standalone_message_reminders

    Injecting `db` (instead of None) keeps everything inside the test
    transaction — same pattern used by TestSendPendingReminders and
    TestSendMessageReminders.
    """

    def _msg(self, db, sender, interval_hours=1, last_sent_hours_ago=2,
             max_reminders=None, reminders_sent=0, is_active=True,
             context=None):
        msg = models.StandaloneMessage(
            sender_id=sender.id,
            to_emails=["dest@x.com"],
            subject="Subject {{key}}",
            message="Body {{key}}",
            context=context,
            reminder_interval_hours=interval_hours,
            max_reminders=max_reminders,
            reminders_sent=reminders_sent,
            last_sent_at=datetime.utcnow() - timedelta(hours=last_sent_hours_ago),
            is_active=is_active,
        )
        db.add(msg)
        db.flush()
        return msg

    def test_resends_when_interval_elapsed(self, db):
        sender = make_user(db, email=f"ssr-{_uid()}@x.com")
        db.commit()
        msg = self._msg(db, sender, interval_hours=1, last_sent_hours_ago=2)
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 1
        assert msg.last_sent_at is not None

    def test_no_resend_before_interval(self, db):
        sender = make_user(db, email=f"ssr-early-{_uid()}@x.com")
        db.commit()
        msg = self._msg(db, sender, interval_hours=4, last_sent_hours_ago=1)
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 0

    def test_deactivates_after_max_reminders_reached(self, db):
        sender = make_user(db, email=f"ssr-max-{_uid()}@x.com")
        db.commit()
        msg = self._msg(db, sender, interval_hours=1, last_sent_hours_ago=2,
                        max_reminders=1, reminders_sent=0)
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 1
        assert msg.is_active is False

    def test_already_at_max_deactivates_without_sending(self, db):
        sender = make_user(db, email=f"ssr-atmax-{_uid()}@x.com")
        db.commit()
        msg = self._msg(db, sender, interval_hours=1, last_sent_hours_ago=2,
                        max_reminders=2, reminders_sent=2)
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 2   # unchanged
        assert msg.is_active is False

    def test_inactive_message_is_skipped(self, db):
        sender = make_user(db, email=f"ssr-skip-{_uid()}@x.com")
        db.commit()
        msg = self._msg(db, sender, interval_hours=1, last_sent_hours_ago=2,
                        is_active=False)
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 0

    def test_context_placeholder_rendered_before_send(self, db):
        """The job renders {{key}} against the stored context dict.
        We can't assert on the rendered email body (SMTP not configured)
        but can confirm reminders_sent is incremented — the rendering
        must not crash even with a context dict present."""
        sender = make_user(db, email=f"ssr-ctx-{_uid()}@x.com")
        db.commit()
        msg = self._msg(db, sender, interval_hours=1, last_sent_hours_ago=2,
                        context={"key": "INV-999"})
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 1

    def test_unlimited_reminders_keeps_active(self, db):
        """max_reminders=None means unlimited — is_active stays True after each send."""
        sender = make_user(db, email=f"ssr-unlim-{_uid()}@x.com")
        db.commit()
        msg = self._msg(db, sender, interval_hours=1, last_sent_hours_ago=2,
                        max_reminders=None, reminders_sent=99)
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 100
        assert msg.is_active is True

    def test_message_without_reminder_interval_is_ignored(self, db):
        """StandaloneMessages created without reminder_interval_hours
        (is_active=False one-shots) must not be picked up by the job."""
        sender = make_user(db, email=f"ssr-nointerval-{_uid()}@x.com")
        db.commit()
        msg = models.StandaloneMessage(
            sender_id=sender.id,
            to_emails=["x@x.com"],
            message="One-shot",
            reminder_interval_hours=None,   # no interval
            reminders_sent=0,
            last_sent_at=datetime.utcnow() - timedelta(hours=10),
            is_active=False,
        )
        db.add(msg)
        db.commit()

        escalation.send_standalone_message_reminders(db)

        db.refresh(msg)
        assert msg.reminders_sent == 0
