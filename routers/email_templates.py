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
    EmailTemplateCreate, EmailTemplateOut, EmailTemplateUpdate, EmailTemplatePreviewOut,
    EmailTemplateTestSend, EmailTemplateTriggerRequest, EmailTemplateRevisionOut,
)
from services.email_templates import render_email_template, send_triggered_email
from template_utils import extract_placeholders
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


# Server-side globals every render always has (services/email_templates.py's
# _global_variables()) — a template can reference these without them appearing in its own
# sample_data.
_GLOBAL_VARIABLES = {"portal_url", "company_name"}


def _template_snapshot(template: models.EmailTemplate) -> dict:
    """Full column dump of a row, JSON-safe — used both as the "current row" view merged with
    an incoming PATCH (for placeholder validation) and as the stored revision snapshot."""
    snap = {}
    for col in template.__table__.columns.keys():
        val = getattr(template, col)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        snap[col] = val
    return snap


def _used_placeholders(fields: dict) -> set:
    used = set()
    for f in ("subject", "preheader", "heading", "intro", "cta_label", "cta_url", "outro"):
        used |= extract_placeholders(fields.get(f))
    for row in (fields.get("detail_rows") or []):
        if row:
            for cell in row:
                used |= extract_placeholders(cell)
    # table_blocks' title renders once per email against the top-level variables, same as
    # heading/intro above — only each column's value_template is row-scoped (see
    # _used_row_placeholders below).
    for block in (fields.get("table_blocks") or []):
        if block:
            used |= extract_placeholders(block.get("title"))
    return used


def _used_row_placeholders(fields: dict) -> dict:
    """{list_variable: set-of-placeholder-names} used across each table block's columns — these
    are checked against that list_variable's sample rows, not the top-level Sample data dict."""
    out: dict = {}
    for block in (fields.get("table_blocks") or []):
        if not block:
            continue
        list_variable = block.get("list_variable")
        if not list_variable:
            continue
        names = out.setdefault(list_variable, set())
        for col in (block.get("columns") or []):
            if col:
                names |= extract_placeholders(col.get("header"))
                names |= extract_placeholders(col.get("value_template"))
    return out


def _same_moment(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a.replace(microsecond=0, tzinfo=None) == b.replace(microsecond=0, tzinfo=None)


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
    attachments = None
    if payload.attachments:
        import base64
        attachments = [
            (a.filename, base64.b64decode(a.content_base64), a.subtype)
            for a in payload.attachments
        ]
    sent = await send_triggered_email(
        db, mail_key, payload.to_email, payload.variables,
        tone_override=payload.tone_override, attachments=attachments,
    )
    return {"sent": sent}


# ─── Admin CRUD: templates ───────────────────────────────────────────────────

@router.get("/", response_model=List[EmailTemplateOut])
def list_templates(user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    return db.query(models.EmailTemplate).order_by(models.EmailTemplate.process_key, models.EmailTemplate.mail_key).all()


@router.post("/", response_model=EmailTemplateOut)
def create_template(
    payload: EmailTemplateCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Stores a new template row and hands back its id — subject/heading start
    as the mail_label (the subject/heading columns are NOT NULL) and everything
    else is blank, ready to fill in through the normal PATCH editor. Nothing
    about this wires the mail_key up to an actual send; that's a separate,
    later step in code (send_triggered_email(mail_key, ...) from wherever the
    trigger point is), same as every other template today."""
    admin = _require_admin(user_id, db)
    if db.query(models.EmailTemplate).filter(models.EmailTemplate.mail_key == payload.mail_key).first():
        raise HTTPException(400, f"mail_key '{payload.mail_key}' is already in use")
    template = models.EmailTemplate(
        process_key=payload.process_key,
        mail_key=payload.mail_key,
        mail_label=payload.mail_label,
        enabled=False,
        subject=payload.mail_label,
        heading=payload.mail_label,
        updated_by_id=admin.id,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.patch("/{template_id}", response_model=EmailTemplateOut)
def update_template(
    template_id: int,
    payload: EmailTemplateUpdate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    admin = _require_admin(user_id, db)
    template = _get_template_or_404(template_id, db)

    updates = payload.dict(exclude_unset=True)
    updates.pop("expected_updated_at", None)

    # Optimistic concurrency: reject a save based on a stale copy instead of silently
    # last-write-wins clobbering whatever another admin saved in between. Skipped when the
    # caller doesn't send expected_updated_at (e.g. an older client) — opt-in, not enforced.
    if payload.expected_updated_at is not None and not _same_moment(template.updated_at, payload.expected_updated_at):
        raise HTTPException(
            409,
            "This template was changed by someone else since you loaded it. Refresh the page "
            "and reapply your edits.",
        )

    if "footer_id" in updates and updates["footer_id"] is not None:
        if not db.query(models.EmailFooter).filter(models.EmailFooter.id == updates["footer_id"]).first():
            raise HTTPException(400, f"Footer id {updates['footer_id']} does not exist.")

    # Validate every {{placeholder}} used against Sample data (merging the current row with
    # this PATCH, so editing just one field still validates the template's real full content) —
    # an unknown one renders as literal "{{typo}}" text in the live email rather than the
    # intended value.
    merged = _template_snapshot(template)
    merged.update(updates)
    sample_data = merged.get("sample_data") or {}
    unknown = _used_placeholders(merged) - set(sample_data.keys()) - _GLOBAL_VARIABLES
    if unknown:
        raise HTTPException(
            400,
            "These placeholders aren't in Sample data, so they'd render as literal text in the "
            f"live email instead of a real value: {', '.join(sorted(unknown))}. Add them to "
            "Sample data with an example value, or fix the typo.",
        )

    # Same check for table-block columns, but scoped per list_variable: a column can reference
    # either that list_variable's own sample row keys or a top-level scalar Sample data value
    # (e.g. a shared currency code) — the real render merges both (see _render_table_blocks).
    scalar_keys = set(sample_data.keys()) | _GLOBAL_VARIABLES
    for list_variable, names in _used_row_placeholders(merged).items():
        sample_rows = sample_data.get(list_variable) or []
        row_keys = set()
        for r in sample_rows:
            if isinstance(r, dict):
                row_keys |= set(r.keys())
        unknown_row = names - row_keys - scalar_keys
        if unknown_row:
            raise HTTPException(
                400,
                f"Table '{list_variable}': these placeholders aren't in that table's sample rows "
                f"or Sample data: {', '.join(sorted(unknown_row))}. Add a sample row with an "
                "example value for each, or fix the typo.",
            )

    # Snapshot the row as it stood right before this edit overwrites it.
    db.add(models.EmailTemplateRevision(
        template_id=template.id,
        snapshot=_template_snapshot(template),
        changed_by_id=admin.id,
    ))

    for field, val in updates.items():
        setattr(template, field, val)
    template.updated_by_id = admin.id
    db.commit()
    db.refresh(template)
    return template


@router.get("/{template_id}/revisions", response_model=List[EmailTemplateRevisionOut])
def list_revisions(template_id: int, user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    _get_template_or_404(template_id, db)
    revisions = (
        db.query(models.EmailTemplateRevision)
        .filter(models.EmailTemplateRevision.template_id == template_id)
        .order_by(models.EmailTemplateRevision.changed_at.desc())
        .all()
    )
    return [
        EmailTemplateRevisionOut(
            id=r.id,
            changed_at=r.changed_at,
            changed_by_id=r.changed_by_id,
            changed_by_email=r.changed_by.email if r.changed_by else None,
            snapshot=r.snapshot,
        )
        for r in revisions
    ]


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
