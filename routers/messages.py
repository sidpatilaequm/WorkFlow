"""
routers/messages.py — Standalone message endpoint (Messaging #4)

POST /api/messages/
  Fire a notification to one or more email addresses without any workflow
  request context. Supports {{key}} template rendering against a caller-
  supplied `context` dict and optional recurring resend (reminder_interval_hours).

GET /api/messages/
  List standalone messages sent by the caller (or all, for admins).

PATCH /api/messages/{id}/deactivate
  Stop recurring sends for a standalone message.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime
import re

from database import get_db
from schemas import StandaloneMessageCreate, StandaloneMessageOut
from services.notification import notification_service
import models
from routers.approvals import _run_async

router = APIRouter()


def _get_user_or_404(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user


def _render_context(template: str, context: dict) -> str:
    """Replace {{key}} placeholders with values from context dict."""
    if not context:
        return template
    def replacer(m):
        key = m.group(1).strip()
        val = context.get(key)
        return str(val) if val is not None else m.group(0)
    return re.sub(r"\{\{(.+?)\}\}", replacer, template)


@router.post("/", response_model=StandaloneMessageOut, status_code=201)
def send_standalone_message(
    payload: StandaloneMessageCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = _get_user_or_404(user_id, db)

    if not payload.to_emails:
        raise HTTPException(400, "to_emails must contain at least one address")

    ctx = payload.context or {}
    rendered_subject = _render_context(payload.subject or "Message", ctx)
    rendered_message = _render_context(payload.message, ctx)

    _run_async(
        notification_service.send_standalone_message(
            to=list(payload.to_emails),
            subject=rendered_subject,
            message=rendered_message,
            sender_name=current_user.name,
        )
    )

    msg = models.StandaloneMessage(
        sender_id=current_user.id,
        to_emails=list(payload.to_emails),
        subject=payload.subject,
        message=payload.message,
        context=ctx or None,
        reminder_interval_hours=payload.reminder_interval_hours,
        max_reminders=payload.max_reminders,
        reminders_sent=0,
        last_sent_at=datetime.utcnow(),
        is_active=payload.reminder_interval_hours is not None,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


@router.get("/", response_model=List[StandaloneMessageOut])
def list_standalone_messages(
    user_id: int = Query(...),
    active_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    """List standalone messages. Admins see all; others see their own."""
    current_user = _get_user_or_404(user_id, db)
    q = db.query(models.StandaloneMessage)
    if not models.is_admin_role(current_user.role):
        q = q.filter(models.StandaloneMessage.sender_id == current_user.id)
    if active_only:
        q = q.filter(models.StandaloneMessage.is_active == True)
    return q.order_by(models.StandaloneMessage.created_at.desc()).all()


@router.patch("/{msg_id}/deactivate", response_model=StandaloneMessageOut)
def deactivate_standalone_message(
    msg_id: int,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Stop recurring sends for a standalone message."""
    current_user = _get_user_or_404(user_id, db)
    msg = db.query(models.StandaloneMessage).filter(
        models.StandaloneMessage.id == msg_id
    ).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg.sender_id != current_user.id and not models.is_admin_role(current_user.role):
        raise HTTPException(403, "Access denied")
    msg.is_active = False
    db.commit()
    db.refresh(msg)
    return msg
