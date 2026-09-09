"""
Admin CRUD for report_schedules — recurring "email this report" sends for
analytics (NexD Designer) reports. The report definition, rendering, and
actual sending all live in the separate analytics service; this only stores
which published report to re-send, to whom, and how often (see
models.ReportSchedule), gated the same way every other admin surface in this
app is (routers/workflows.py's _require_admin: user_id query param ->
models.is_admin_role — WorkFlow already has real per-user auth vendor_portal
calls directly, same as email_templates.py's CRUD below).

The actual periodic check/send lives in services/report_schedules.py,
registered on its own BackgroundScheduler in main.py's startup — deliberately
NOT the same scheduler instance services/escalation.py defines (that one's
start_scheduler() is never actually called anywhere in this codebase today;
wiring a new job onto it would silently also activate escalation/reminder
sends that have apparently never fired in production, which is a separate
decision, not something to do as a side effect of this feature).
"""
import os
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas import AvailableReportOut, ReportScheduleCreate, ReportScheduleOut, ReportScheduleUpdate
import models

router = APIRouter()

ANALYTICS_BASE_URL = os.getenv("ANALYTICS_BASE_URL", "http://127.0.0.1:5090")
WORKFLOW_SERVICE_TOKEN = os.getenv("JAVA_SERVICE_TOKEN")  # same shared secret, reused for this direction too


def _require_admin(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if not models.is_admin_role(user.role):
        raise HTTPException(403, "Admin role required")
    return user


@router.get("/available-reports", response_model=List[AvailableReportOut])
def available_reports(user_id: int = Query(...), db: Session = Depends(get_db)):
    """Every live published report link in analytics, for the "pick a report"
    dropdown — proxied server-to-server so vendor_portal's admin session
    (a WorkFlow/backend_java JWT) never needs to know about analytics'
    completely separate login system."""
    _require_admin(user_id, db)
    if not WORKFLOW_SERVICE_TOKEN:
        raise HTTPException(500, "JAVA_SERVICE_TOKEN is not configured")
    try:
        resp = httpx.get(
            f"{ANALYTICS_BASE_URL}/api/internal/reports",
            headers={"X-Service-Token": WORKFLOW_SERVICE_TOKEN},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"could not reach analytics: {exc}")


@router.get("/", response_model=List[ReportScheduleOut])
def list_schedules(user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    return db.query(models.ReportSchedule).order_by(models.ReportSchedule.created_at.desc()).all()


@router.post("/", response_model=ReportScheduleOut)
def create_schedule(payload: ReportScheduleCreate, user_id: int = Query(...), db: Session = Depends(get_db)):
    admin = _require_admin(user_id, db)
    if payload.interval_hours < 1:
        raise HTTPException(400, "interval_hours must be at least 1")
    if not payload.recipients:
        raise HTTPException(400, "at least one recipient is required")
    sched = models.ReportSchedule(
        process_key=payload.process_key,
        token=payload.token,
        role=payload.role,
        report_name=payload.report_name,
        recipients=payload.recipients,
        interval_hours=payload.interval_hours,
        message=payload.message,
        created_by=admin.id,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return sched


@router.patch("/{schedule_id}", response_model=ReportScheduleOut)
def update_schedule(
    schedule_id: int,
    payload: ReportScheduleUpdate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
    sched = db.query(models.ReportSchedule).filter(models.ReportSchedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "No such schedule")

    updates = payload.dict(exclude_unset=True)
    if "interval_hours" in updates and updates["interval_hours"] < 1:
        raise HTTPException(400, "interval_hours must be at least 1")
    if "recipients" in updates and not updates["recipients"]:
        raise HTTPException(400, "at least one recipient is required")
    for field, val in updates.items():
        setattr(sched, field, val)
    db.commit()
    db.refresh(sched)
    return sched


@router.delete("/{schedule_id}")
def delete_schedule(schedule_id: int, user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    sched = db.query(models.ReportSchedule).filter(models.ReportSchedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "No such schedule")
    db.delete(sched)
    db.commit()
    return {"deleted": True}
