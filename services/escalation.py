"""
Escalation Service
APScheduler jobs: SLA breach escalation + pending-approver reminders.
Ported from workflow_engine, adapted to Vendors_Workflow sync ORM + models.
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
            # Notify admins
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
                          <p>Request <strong>#{req_id}</strong> — <strong>{req.document_name or req.title}</strong>
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


def send_pending_reminders() -> None:
    """Warn approvers 4 hours before their SLA expires."""
    import asyncio
    from database import SessionLocal
    import models
    from services.notification import notification_service

    logger.info("Running pending reminder check...")
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        warn_threshold = now + timedelta(hours=4)

        near_due_stages = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.status == models.RequestStatus.pending,
                models.RequestStage.sla_deadline <= warn_threshold,
                models.RequestStage.sla_deadline > now,
                models.RequestStage.is_sla_breached == False,
                models.RequestStage.started_at.isnot(None),
            )
            .all()
        )

        for rs in near_due_stages:
            req = db.query(models.WorkflowRequest).filter(
                models.WorkflowRequest.id == rs.request_id
            ).first()
            if not req:
                continue

            stage_def = db.query(models.WorkflowStage).filter(
                models.WorkflowStage.id == rs.stage_id
            ).first()
            if not stage_def or not stage_def.approver_group_id:
                continue

            members = (
                db.query(models.ApproverGroupMember)
                .filter(models.ApproverGroupMember.group_id == stage_def.approver_group_id)
                .all()
            )
            for member in members:
                user = db.query(models.User).filter(models.User.id == member.user_id).first()
                if user and user.email:
                    asyncio.run(notification_service.send_email(
                        to=[user.email],
                        subject=f"[Reminder] Pending approval: {req.document_name or req.title}",
                        html_body=f"""
                        <div style="font-family:sans-serif">
                          <h3>Approval reminder</h3>
                          <p>{req.document_name or req.title} is awaiting your decision.
                          SLA expires at {rs.sla_deadline.strftime('%Y-%m-%d %H:%M UTC')}.</p>
                          <p><a href="{FRONTEND_URL}/requests/{req.id}">Review &#8594;</a></p>
                        </div>
                        """,
                    ))
    except Exception as exc:
        db.rollback()
        logger.error("Reminder check failed: %s", exc)
    finally:
        db.close()


def start_scheduler() -> None:
    scheduler.add_job(
        run_escalation_check,
        trigger=IntervalTrigger(minutes=ESCALATION_CHECK_INTERVAL),
        id="escalation_check",
        replace_existing=True,
    )
    scheduler.add_job(
        send_pending_reminders,
        trigger=IntervalTrigger(hours=1),
        id="pending_reminders",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started (escalation every %dm)", ESCALATION_CHECK_INTERVAL)


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
