"""
Escalation Service
APScheduler jobs:
  1. run_escalation_check  — marks overdue stages as SLA-breached, escalates requests.
  2. send_pending_reminders — per-workflow configurable reminders sent to every approver
     in the current stage's group who has NOT yet acted, starting reminder_after_hours
     after the stage began and repeating every reminder_interval_hours thereafter.
     Writes an ActivityLog entry (action="reminder_sent") per recipient so the
     notification report endpoint can count bypassed vs non-bypassed stages.
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

def run_escalation_check() -> None:
    """Mark overdue RequestStages as SLA-breached and escalate their requests."""
    import asyncio
    from database import SessionLocal
    import models
    from services.notification import notification_service

    logger.info("Running SLA escalation check...")
    db = SessionLocal()
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

        db.commit()

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
        db.rollback()
        logger.error("Escalation check failed: %s", exc)
    finally:
        db.close()


# ─── Job 2: Configurable pending-approver reminders ───────────────────────────

def send_pending_reminders() -> None:
    """
    For every pending RequestStage whose workflow has reminder settings configured:

      reminder_after_hours    — first reminder fires this many hours after stage start.
      reminder_interval_hours — subsequent reminders fire every N hours after that.

    Only approvers who have NOT yet acted on the stage receive the email.
    Writes ActivityLog(action="reminder_sent") per recipient so the reporting
    endpoint can distinguish stages where everyone acted before reminders were
    needed (bypassed) from those where reminders had to fire (did not bypass).
    """
    import asyncio
    from database import SessionLocal
    import models
    from services.notification import notification_service

    logger.info("Running pending reminder check...")
    db = SessionLocal()
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

            stage_def = db.query(models.WorkflowStage).filter(
                models.WorkflowStage.id == rs.stage_id
            ).first()
            if not stage_def or not stage_def.approver_group_id:
                continue

            already_acted_ids = {
                a.approver_id
                for a in db.query(models.ApprovalAction)
                .filter(models.ApprovalAction.request_stage_id == rs.id)
                .all()
            }

            pending_members = (
                db.query(models.ApproverGroupMember)
                .filter(
                    models.ApproverGroupMember.group_id == stage_def.approver_group_id,
                    models.ApproverGroupMember.user_id.notin_(already_acted_ids)
                    if already_acted_ids else True,
                )
                .all()
            )
            if not pending_members:
                continue

            doc_label   = req.document_name or req.title
            request_url = f"{FRONTEND_URL}/requests/{req.id}"
            sla_text    = (
                rs.sla_deadline.strftime("%Y-%m-%d %H:%M UTC")
                if rs.sla_deadline else "No SLA set"
            )

            sent_to_user_ids = []
            for member in pending_members:
                user = db.query(models.User).filter(models.User.id == member.user_id).first()
                if not user or not user.email:
                    continue

                html_body = f"""
                <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
                  <h2 style="color:#E24B4A">Pending Approval Reminder</h2>
                  <p>Hi {user.firstName or "there"},</p>
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
                      <td style="padding:8px;border:1px solid #eee">{stage_def.name} (Stage {rs.stage_order})</td>
                    </tr>
                    <tr>
                      <td style="padding:8px;border:1px solid #eee;font-weight:bold">SLA Deadline</td>
                      <td style="padding:8px;border:1px solid #eee">{sla_text}</td>
                    </tr>
                  </table>
                  <p>The document is currently waiting at the
                  <strong>{stage_def.name}</strong> stage and requires your decision.</p>
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
                    to=[user.email],
                    subject=f"[Reminder] Approval pending: {doc_label} — {stage_def.name}",
                    html_body=html_body,
                ))
                logger.info(
                    "Reminder sent to %s for request #%d stage %d",
                    user.email, req.id, rs.stage_order,
                )
                sent_to_user_ids.append(user.id)

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
                        f"'{stage_def.name}' (order {rs.stage_order})"
                    ),
                    stage_order=rs.stage_order,
                    extra={
                        "request_stage_id": rs.id,
                        "stage_name": stage_def.name,
                        "workflow_id": workflow.id,
                        "workflow_name": workflow.name,
                        "reminded_user_id": uid,
                    },
                ))

            rs.last_reminded_at = now

        db.commit()

    except Exception as exc:
        db.rollback()
        logger.error("Reminder check failed: %s", exc)
    finally:
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
    scheduler.start()
    logger.info("Scheduler started (escalation every %dm)", ESCALATION_CHECK_INTERVAL)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
