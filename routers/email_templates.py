"""
routers/email_templates.py — admin-editable transactional email templates.

POST /api/email-templates/trigger/{mail_key}
  Server-to-server: backend_java calls this to render + send one of the 3
  live templates (VO.2 draft-saved, VO.6 supplier-approved). Guarded by a
  static shared-secret header rather than the user_id-based admin auth below
  — there is no logged-in WorkFlow user on the Java side of this call, and
  today's other Java->WorkFlow calls (POST /api/requests/) have no auth at
  all, which this at least closes for the one endpoint that can send email
  to an attacker-controllable address.

GET/PATCH /api/email-templates/, /api/email-templates/footers/
  Admin CRUD for the 3 templates + shared footer library, gated the same way
  as every other admin surface in this app (routers/workflows.py's
  _require_admin: user_id query param -> models.is_admin_role). No Java
  proxy — WorkFlow already has real per-user auth that AdminWorkflows.jsx
  already calls directly for identical CRUD today.

POST /api/email-templates/{id}/preview   — render with sample_data, no send
POST /api/email-templates/{id}/test-send — render with sample_data, send to
  an admin-supplied address
"""
import os
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from schemas import (
    EmailFooterOut, EmailFooterUpdate,
    EmailTemplateOut, EmailTemplateUpdate, EmailTemplatePreviewOut,
    EmailTemplateTestSend, EmailTemplateTriggerRequest,
)
from services.email_templates import render_email_template, send_triggered_email
import models

router = APIRouter()

JAVA_SERVICE_TOKEN = os.getenv("JAVA_SERVICE_TOKEN")


def _get_user_or_404(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user


def _require_admin(user_id: int, db: Session) -> models.User:
    user = _get_user_or_404(user_id, db)
    if not models.is_admin_role(user.role):
        raise HTTPException(403, "Admin role required")
    return user


def _get_template_or_404(template_id: int, db: Session) -> models.EmailTemplate:
    template = db.query(models.EmailTemplate).filter(models.EmailTemplate.id == template_id).first()
    if not template:
        raise HTTPException(404, "Email template not found")
    return template


# ─── Server-to-server trigger (backend_java) ────────────────────────────────

@router.post("/trigger/{mail_key}")
async def trigger_email(
    mail_key: str,
    payload: EmailTemplateTriggerRequest,
    db: Session = Depends(get_db),
    x_service_token: str = Header(default=None),
):
    if not JAVA_SERVICE_TOKEN or x_service_token != JAVA_SERVICE_TOKEN:
        raise HTTPException(401, "Invalid or missing service token")
    sent = await send_triggered_email(db, mail_key, payload.to_email, payload.variables)
    return {"sent": sent}


# ─── Admin CRUD: templates ───────────────────────────────────────────────────

@router.get("/", response_model=List[EmailTemplateOut])
def list_templates(user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    return db.query(models.EmailTemplate).order_by(models.EmailTemplate.process_key, models.EmailTemplate.mail_key).all()


@router.patch("/{template_id}", response_model=EmailTemplateOut)
def update_template(
    template_id: int,
    payload: EmailTemplateUpdate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    admin = _require_admin(user_id, db)
    template = _get_template_or_404(template_id, db)
    for field, val in payload.dict(exclude_unset=True).items():
        setattr(template, field, val)
    template.updated_by_id = admin.id
    db.commit()
    db.refresh(template)
    return template


@router.post("/{template_id}/preview", response_model=EmailTemplatePreviewOut)
def preview_template(template_id: int, user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    template = _get_template_or_404(template_id, db)
    subject, html_body, text_body = render_email_template(template, template.sample_data or {})
    return EmailTemplatePreviewOut(subject=subject, html=html_body, text=text_body)


@router.post("/{template_id}/test-send")
async def test_send_template(
    template_id: int,
    payload: EmailTemplateTestSend,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
    template = _get_template_or_404(template_id, db)
    subject, html_body, text_body = render_email_template(template, template.sample_data or {})
    from services.notification import notification_service
    sent = await notification_service.send_email(to=[payload.to_email], subject=subject, html_body=html_body, text_body=text_body)
    return {"sent": sent}


# ─── Admin CRUD: shared footer library ──────────────────────────────────────

@router.get("/footers/", response_model=List[EmailFooterOut])
def list_footers(user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    return db.query(models.EmailFooter).all()


@router.patch("/footers/{footer_id}", response_model=EmailFooterOut)
def update_footer(
    footer_id: int,
    payload: EmailFooterUpdate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
    footer = db.query(models.EmailFooter).filter(models.EmailFooter.id == footer_id).first()
    if not footer:
        raise HTTPException(404, "Footer not found")
    for field, val in payload.dict(exclude_unset=True).items():
        setattr(footer, field, val)
    db.commit()
    db.refresh(footer)
    return footer
