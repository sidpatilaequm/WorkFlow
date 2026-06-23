"""
Escalation Service
APScheduler jobs:
  1. run_escalation_check   — marks overdue stages as SLA-breached, escalates requests.
  2. send_pending_reminders — per-workflow configurable reminders sent to every approver
     in the current stage's group who has NOT yet acted, starting reminder_after_hours
     after the stage began and repeating every reminder_interval_hours thereafter.
     Writes an ActivityLog entry (action="reminder_sent") per recipient so the
     notification report endpoint can count bypassed vs non-bypassed stages.
     Membership is resolved via workflow_snapshot.get_stage_config so a stage's
     reminder recipients match the approver group the request was actually
     submitted under, not whatever the live group looks like today.
  3. send_message_reminders — resends ScheduledMessage rows created by
     POST /requests/{id}/send-message (Messaging #2) every
     reminder_interval_hours, for as long as the parent request is still
     'pending' and reminders_sent < max_reminders (if set). Recipients for
     to="current_approvers" are also resolved via the frozen workflow
     snapshot, for the same reason as job 2.
"""
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ESCALATION_CHECK_INTERVAL = int(os.getenv("ESCALATION_CHECK_INTERVAL", "15"))
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

scheduler = BackgroundScheduler()


# ─── Job 1: SLA breach escalation ─────────────────────────────────────────────

def run_escalation_check(db=None) -> None:
    """
    Mark overdue RequestStages as SLA-breached and escalate their requests.

    `db` is normally left as None (the scheduler's real call path), which
    opens and owns its own session/transaction. Tests can inject the
    fixture's session instead so everything runs in the same transaction
    the test controls — when `db` is supplied externally, this function
    flushes instead of committing and leaves closing/rollback to the caller.
    """
    import asyncio
    from database import SessionLocal
    import models
    from services.notification import notification_service

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    logger.info("Running SLA escalation check...")
    try:
        now = datetime.utcnow()
        overdue_stages = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.status == models.RequestStatus.pending,
                models.RequestStage.sla_deadline <= now,
                models.RequestStage.sla_deadline.isnot(None),
                models.RequestStage.is_sla_breached == False,
                models.RequestStage.started_at.isnot(None),
            )
            .all()
        )

        escalated_request_ids = set()
        for stage in overdue_stages:
            stage.is_sla_breached = True
            req = db.query(models.WorkflowRequest).filter(
                models.WorkflowRequest.id == stage.request_id
            ).first()
            if req and req.status == models.RequestStatus.pending:
                req.status = models.RequestStatus.escalated
                escalated_request_ids.add(req.id)
                db.add(models.ActivityLog(
                    request_id=req.id,
                    user_id=None,
                    action="escalated",
                    detail=f"SLA exceeded at stage {stage.stage_order}. Deadline was {stage.sla_deadline}",
                ))

        if owns_session:
            db.commit()
        else:
            db.flush()

        if escalated_request_ids:
            logger.info("Escalated %d request(s): %s", len(escalated_request_ids), escalated_request_ids)
            admins = (
                db.query(models.User)
                .filter(models.User.role == models.UserRole.admin, models.User.is_active == True)
                .all()
            )
            admin_emails = [u.email for u in admins]
            for req_id in escalated_request_ids:
                req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
                if req and admin_emails:
                    asyncio.run(notification_service.send_email(
                        to=admin_emails,
                        subject=f"[ESCALATED] {req.document_name or req.title} — SLA Breach",
                        html_body=f"""
                        <div style="font-family:sans-serif">
                          <h2 style="color:#E24B4A">SLA Breach Escalation</h2>
                          <p>Request <strong>#{req_id}</strong> —
                          <strong>{req.document_name or req.title}</strong>
                          has exceeded its SLA and has been escalated.</p>
                          <p><a href="{FRONTEND_URL}/requests/{req_id}">Review now &#8594;</a></p>
                        </div>
                        """,
                    ))
    except Exception as exc:
        if owns_session:
            db.rollback()
        logger.error("Escalation check failed: %s", exc)
        if not owns_session:
            raise
    finally:
        if owns_session:
            db.close()


# ─── Job 2: Configurable pending-approver reminders ───────────────────────────

def send_pending_reminders(db=None) -> None:
    """
    For every pending RequestStage whose workflow has reminder settings configured:

      reminder_after_hours    — first reminder fires this many hours after stage start.
      reminder_interval_hours — subsequent reminders fire every N hours after that.

    Only approvers who have NOT yet acted on the stage receive the email.
    Writes ActivityLog(action="reminder_sent") per recipient so the reporting
    endpoint can distinguish stages where everyone acted before reminders were
    needed (bypassed) from those where reminders had to fire (did not bypass).

    `db` follows the same injectable-session convention as run_escalation_check
    (see its docstring) — None in production, an externally supplied session
    in tests.
    """
    import asyncio
    from database import SessionLocal
    import models
    from services.notification import notification_service
    from workflow_snapshot import get_stage_config

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    logger.info("Running pending reminder check...")
    try:
        now = datetime.utcnow()

        pending_stages = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.status == models.RequestStatus.pending,
                models.RequestStage.started_at.isnot(None),
            )
            .all()
        )

        for rs in pending_stages:
            req = db.query(models.WorkflowRequest).filter(
                models.WorkflowRequest.id == rs.request_id
            ).first()
            if not req or req.status != models.RequestStatus.pending:
                continue

            workflow = db.query(models.Workflow).filter(
                models.Workflow.id == req.workflow_id
            ).first()
            if not workflow or workflow.reminder_after_hours is None:
                continue

            reminder_after    = workflow.reminder_after_hours
            reminder_interval = workflow.reminder_interval_hours

            stage_started = rs.started_at
            if stage_started.tzinfo is not None:
                stage_started = stage_started.replace(tzinfo=None)

            should_remind = False
            if rs.last_reminded_at is None:
                first_due = stage_started + timedelta(hours=reminder_after)
                if now >= first_due:
                    should_remind = True
            elif reminder_interval is not None and reminder_interval > 0:
                last = rs.last_reminded_at
                if last.tzinfo is not None:
                    last = last.replace(tzinfo=None)
                if now >= last + timedelta(hours=reminder_interval):
                    should_remind = True

            if not should_remind:
                continue

            # Frozen-at-submission stage config (falls back to live tables
            # for pre-snapshot requests) — see workflow_snapshot.py. This is
            # what keeps reminder recipients matching the approver group the
            # request was actually submitted under, even if an admin later
            # edits or reorders the live group.
            stage_cfg = get_stage_config(db, req, rs.stage_order, rs.stage_id)
            if not stage_cfg or not stage_cfg.get("members"):
                continue

            already_acted_ids = {
                a.approver_id
                for a in db.query(models.ApprovalAction)
                .filter(models.ApprovalAction.request_stage_id == rs.id)
                .all()
            }

            pending_members = [
                m for m in stage_cfg.get("members", [])
                if m["user_id"] not in already_acted_ids and m.get("email")
            ]
            if not pending_members:
                continue

            doc_label   = req.document_name or req.title
            request_url = f"{FRONTEND_URL}/requests/{req.id}"
            sla_text    = (
                rs.sla_deadline.strftime("%Y-%m-%d %H:%M UTC")
                if rs.sla_deadline else "No SLA set"
            )

            stage_name = stage_cfg.get("name") or "Unknown stage"

            sent_to_user_ids = []
            for member in pending_members:
                first_name = (member.get("name") or "there").split(" ")[0]

                html_body = f"""
                <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
                  <h2 style="color:#E24B4A">Pending Approval Reminder</h2>
                  <p>Hi {first_name},</p>
                  <p>You have not yet approved the following document.
                  Your action is required.</p>
                  <table style="border-collapse:collapse;width:100%;margin:16px 0">
                    <tr>
                      <td style="padding:8px;border:1px solid #eee;font-weight:bold;width:140px">Document</td>
                      <td style="padding:8px;border:1px solid #eee">{doc_label}</td>
                    </tr>
                    <tr>
                      <td style="padding:8px;border:1px solid #eee;font-weight:bold">Workflow</td>
                      <td style="padding:8px;border:1px solid #eee">{workflow.name}</td>
                    </tr>
                    <tr>
                      <td style="padding:8px;border:1px solid #eee;font-weight:bold">Current Stage</td>
                      <td style="padding:8px;border:1px solid #eee">{stage_name} (Stage {rs.stage_order})</td>
                    </tr>
                    <tr>
                      <td style="padding:8px;border:1px solid #eee;font-weight:bold">SLA Deadline</td>
                      <td style="padding:8px;border:1px solid #eee">{sla_text}</td>
                    </tr>
                  </table>
                  <p>The document is currently waiting at the
                  <strong>{stage_name}</strong> stage and requires your decision.</p>
                  <div style="margin:24px 0">
                    <a href="{request_url}"
                       style="background:#4F7DFF;color:white;padding:12px 28px;
                              text-decoration:none;border-radius:6px;font-weight:600">
                      Review &amp; Act &#8594;
                    </a>
                  </div>
                  <p style="color:#888;font-size:12px">
                    Direct link: <a href="{request_url}">{request_url}</a>
                  </p>
                </div>
                """

                asyncio.run(notification_service.send_email(
                    to=[member["email"]],
                    subject=f"[Reminder] Approval pending: {doc_label} — {stage_name}",
                    html_body=html_body,
                ))
                logger.info(
                    "Reminder sent to %s for request #%d stage %d",
                    member["email"], req.id, rs.stage_order,
                )
                sent_to_user_ids.append(member["user_id"])

            # ── Write one ActivityLog entry per reminder recipient ─────────
            # action="reminder_sent"
            # extra stores who was reminded and which stage, so the reporting
            # endpoint can count bypassed (no reminder_sent) vs not (has reminder_sent).
            for uid in sent_to_user_ids:
                db.add(models.ActivityLog(
                    request_id=req.id,
                    user_id=uid,
                    action="reminder_sent",
                    detail=(
                        f"Reminder sent to user #{uid} for stage "
                        f"'{stage_name}' (order {rs.stage_order})"
                    ),
                    stage_order=rs.stage_order,
                    extra={
                        "request_stage_id": rs.id,
                        "stage_name": stage_name,
                        "workflow_id": workflow.id,
                        "workflow_name": workflow.name,
                        "reminded_user_id": uid,
                    },
                ))

            rs.last_reminded_at = now

        if owns_session:
            db.commit()
        else:
            db.flush()

    except Exception as exc:
        if owns_session:
            db.rollback()
        logger.error("Reminder check failed: %s", exc)
        if not owns_session:
            raise
    finally:
        if owns_session:
            db.close()


# ─── Job 3: Ad-hoc message reminders (Messaging #2) ───────────────────────────

def _resolve_scheduled_recipients(db, req, sm) -> list:
    """
    Mirror of routers/requests.py:_resolve_message_recipients, but for the
    re-send path. "current_approvers" is resolved via the frozen
    workflow_snapshot (workflow_snapshot.get_stage_config) rather than the
    live WorkflowStage/ApproverGroupMember tables, for the same reason
    job 2 above does: a request's reminder recipients shouldn't drift just
    because an admin edited the group after the request was submitted.
    """
    import models
    from workflow_snapshot import get_stage_config

    if sm.to == "submitter":
        submitter = db.query(models.User).filter(models.User.id == req.submitter_id).first()
        return [submitter.email] if submitter and submitter.email else []

    if sm.to == "current_approvers":
        stage_cfg = get_stage_config(db, req, req.current_stage)
        if not stage_cfg:
            return []
        return [m["email"] for m in stage_cfg.get("members", []) if m.get("email")]

    if sm.to == "custom":
        return sm.custom_emails or []

    return []


def send_message_reminders(db=None) -> None:
    """
    Re-send active ScheduledMessage rows on their configured
    reminder_interval_hours cadence, for as long as:
      - the parent request is still 'pending' (the status the message was
        meant to prompt hasn't been achieved yet — once it resolves, the
        ScheduledMessage is deactivated), and
      - max_reminders is unset, or reminders_sent < max_reminders.

    Each send re-renders the template against the request's *current*
    field/metadata values (so a derived amount that changed since the
    original send reflects the latest figure), increments reminders_sent,
    and writes an ActivityLog entry (action="scheduled_message_sent").

    `db` follows the same injectable-session convention as run_escalation_check
    (see its docstring) — None in production, an externally supplied session
    in tests.
    """
    import asyncio
    from database import SessionLocal
    import models
    from services.notification import notification_service
    from template_utils import resolve_template_variables, render_template

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    logger.info("Running scheduled message reminder check...")
    try:
        now = datetime.utcnow()

        active_messages = (
            db.query(models.ScheduledMessage)
            .filter(models.ScheduledMessage.is_active == True)
            .all()
        )

        for sm in active_messages:
            req = db.query(models.WorkflowRequest).filter(
                models.WorkflowRequest.id == sm.request_id
            ).first()

            # The triggering condition no longer holds (request resolved) —
            # stop resending and deactivate.
            if not req or req.status != models.RequestStatus.pending:
                sm.is_active = False
                continue

            if sm.max_reminders is not None and sm.reminders_sent >= sm.max_reminders:
                sm.is_active = False
                continue

            last_sent = sm.last_sent_at or sm.created_at
            if last_sent.tzinfo is not None:
                last_sent = last_sent.replace(tzinfo=None)
            if now < last_sent + timedelta(hours=sm.reminder_interval_hours):
                continue

            recipients = _resolve_scheduled_recipients(db, req, sm)
            if not recipients:
                logger.warning(
                    "ScheduledMessage #%d: no recipients resolved (to=%s), skipping this cycle",
                    sm.id, sm.to,
                )
                continue

            workflow = db.query(models.Workflow).filter(
                models.Workflow.id == req.workflow_id
            ).first()
            variables = resolve_template_variables(req, workflow)
            rendered_message = render_template(sm.message, variables)
            rendered_subject = render_template(sm.subject, variables)

            sender = db.query(models.User).filter(models.User.id == sm.sender_id).first()

            asyncio.run(notification_service.notify_custom_message(
                to=recipients,
                request=req,
                message=rendered_message,
                sender_name=sender.name if sender else "System",
                subject=rendered_subject,
            ))
            logger.info(
                "Scheduled message #%d re-sent for request #%d to %s",
                sm.id, req.id, recipients,
            )

            sm.reminders_sent = (sm.reminders_sent or 0) + 1
            sm.last_sent_at = now
            if sm.max_reminders is not None and sm.reminders_sent >= sm.max_reminders:
                sm.is_active = False

            db.add(models.ActivityLog(
                request_id=req.id,
                user_id=sm.sender_id,
                action="scheduled_message_sent",
                detail=(
                    f"Scheduled message re-sent to {sm.to} "
                    f"({len(recipients)} recipient(s)), "
                    f"reminder #{sm.reminders_sent}"
                ),
                extra={
                    "scheduled_message_id": sm.id,
                    "to": sm.to,
                    "recipients": recipients,
                    "reminders_sent": sm.reminders_sent,
                    "max_reminders": sm.max_reminders,
                },
            ))

        if owns_session:
            db.commit()
        else:
            db.flush()

    except Exception as exc:
        if owns_session:
            db.rollback()
        logger.error("Scheduled message reminder check failed: %s", exc)
        if not owns_session:
            raise
    finally:
        if owns_session:
            db.close()


# ─── Job 4: Standalone message reminders (Messaging #4) ──────────────────────

def send_standalone_message_reminders(db=None) -> None:
    """
    Re-send active StandaloneMessage rows on their configured
    reminder_interval_hours cadence, for as long as:
      - is_active is True (can be stopped via PATCH /api/messages/{id}/deactivate), and
      - max_reminders is unset, or reminders_sent < max_reminders.

    Each send re-renders the message/subject against the StandaloneMessage's
    own stored context dict (which is fixed at creation time — there is no
    live request to re-evaluate against).

    `db` follows the same injectable-session convention as run_escalation_check.
    """
    import asyncio
    from database import SessionLocal
    import models
    from services.notification import notification_service
    import re

    def _render(template, context):
        if not template or not context:
            return template or ""
        def replacer(m):
            key = m.group(1).strip()
            val = context.get(key)
            return str(val) if val is not None else m.group(0)
        return re.sub(r"\{\{(.+?)\}\}", replacer, template)

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    logger.info("Running standalone message reminder check...")
    try:
        now = datetime.utcnow()

        active_messages = (
            db.query(models.StandaloneMessage)
            .filter(
                models.StandaloneMessage.is_active == True,
                models.StandaloneMessage.reminder_interval_hours.isnot(None),
            )
            .all()
        )

        for sm in active_messages:
            if sm.max_reminders is not None and sm.reminders_sent >= sm.max_reminders:
                sm.is_active = False
                continue

            last_sent = sm.last_sent_at or sm.created_at
            if last_sent.tzinfo is not None:
                last_sent = last_sent.replace(tzinfo=None)
            if now < last_sent + timedelta(hours=sm.reminder_interval_hours):
                continue

            ctx = sm.context or {}
            rendered_subject = _render(sm.subject or "Message", ctx)
            rendered_message = _render(sm.message, ctx)

            sender = db.query(models.User).filter(models.User.id == sm.sender_id).first()
            sender_name = sender.name if sender else "System"

            asyncio.run(notification_service.send_standalone_message(
                to=sm.to_emails,
                subject=rendered_subject,
                message=rendered_message,
                sender_name=sender_name,
            ))
            logger.info(
                "Standalone message #%d re-sent to %s",
                sm.id, sm.to_emails,
            )

            sm.reminders_sent = (sm.reminders_sent or 0) + 1
            sm.last_sent_at = now
            if sm.max_reminders is not None and sm.reminders_sent >= sm.max_reminders:
                sm.is_active = False

        if owns_session:
            db.commit()
        else:
            db.flush()

    except Exception as exc:
        if owns_session:
            db.rollback()
        logger.error("Standalone message reminder check failed: %s", exc)
        if not owns_session:
            raise
    finally:
        if owns_session:
            db.close()


# ─── Scheduler bootstrap ──────────────────────────────────────────────────────

def start_scheduler() -> None:
    scheduler.add_job(
        run_escalation_check,
        trigger=IntervalTrigger(minutes=ESCALATION_CHECK_INTERVAL),
        id="run_escalation_check",
        replace_existing=True,
    )
    scheduler.add_job(
        send_pending_reminders,
        # Run every 30 min so short reminder_interval_hours values are honoured.
        # Actual sends are gated by the per-stage last_reminded_at logic above.
        trigger=IntervalTrigger(minutes=30),
        id="send_pending_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        send_message_reminders,
        # Same cadence rationale as send_pending_reminders — actual sends are
        # gated by each ScheduledMessage's own reminder_interval_hours.
        trigger=IntervalTrigger(minutes=30),
        id="send_message_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        send_standalone_message_reminders,
        # Standalone messages (Messaging #4): re-sends gated by each
        # StandaloneMessage's own reminder_interval_hours.
        trigger=IntervalTrigger(minutes=30),
        id="send_standalone_message_reminders",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started (escalation every %dm)", ESCALATION_CHECK_INTERVAL)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
