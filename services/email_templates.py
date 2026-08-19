"""
Rendering + sending for admin-editable transactional emails (email_templates
table). Ports buildEmail()/buildText() from the "Workflow Email Templates"
admin UI mockup almost 1:1 — same 600px table-based HTML shell, masthead,
optional coloured status strip, heading, paragraph-split intro, optional
detail table, optional CTA button with a plaintext fallback link, outro and
footer — so what the admin sees in the preview pane is exactly what a
recipient's inbox renders.

send_triggered_email() is the single entry point both the HTTP trigger
endpoint (routers/email_templates.py, called by backend_java) and
WorkFlow-native call sites (routers/requests.py's submit_request, for VO.4)
go through.
"""
import html
import logging
import os
import re
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy.orm import Session

import models
from services.notification import notification_service
from template_utils import render_template

load_dotenv()
logger = logging.getLogger(__name__)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
COMPANY_NAME = os.getenv("EMAIL_COMPANY_NAME", "Ankit Aerospace Private Limited")

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
_MONO = "'SFMono-Regular',Consolas,'Liberation Mono',Menlo,monospace"

_TONES = {
    "info": {"bg": "#e3eef9", "ink": "#10508c", "edge": "#cadcee"},
    "ok":   {"bg": "#dfefe8", "ink": "#1c6047", "edge": "#c6e0d4"},
    "warn": {"bg": "#f7efd8", "ink": "#7d6413", "edge": "#ecdfb8"},
    "bad":  {"bg": "#fae9e6", "ink": "#b03225", "edge": "#f0cfc9"},
}


def _global_variables() -> dict:
    """Merged into every render call so trigger callers never need to pass
    the portal URL or company name themselves."""
    return {"portal_url": FRONTEND_URL, "company_name": COMPANY_NAME}


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _paragraphs(text: Optional[str], variables: dict) -> str:
    if not text:
        return ""
    blocks = re.split(r"\n\s*\n", text)
    out = []
    for block in blocks:
        if not block.strip():
            continue
        rendered = _esc(render_template(block, variables)).replace("\n", "<br />")
        out.append(
            f'<p style="font-family:{_FONT};font-size:15px;line-height:1.6;'
            f'color:#15222e;margin:0 0 16px;">{rendered}</p>'
        )
    return "".join(out)


def render_email_template(template: "models.EmailTemplate", variables: dict) -> tuple[str, str, str]:
    """Returns (subject, html_body, text_body) for one EmailTemplate row,
    rendering every {{merge_tag}} field against `variables` merged with the
    server-side globals (portal_url, company_name)."""
    v = {**_global_variables(), **(variables or {})}
    tone = _TONES.get(template.status_strip_tone or "info", _TONES["info"])

    footer = template.footer
    if template.footer_override_reason:
        foot_text = template.footer_override_reason
        foot_legal = ""
    elif footer:
        foot_text = footer.reason_text
        foot_legal = footer.legal_line or ""
    else:
        foot_text = ""
        foot_legal = ""

    subject = render_template(template.subject, v) or ""
    preheader = render_template(template.preheader, v) or ""
    heading = render_template(template.heading, v) or ""
    status_text = render_template(template.status_strip_text, v) if template.status_strip_text else None

    rows = [r for r in (template.detail_rows or []) if r and r[0]]
    details_html = "".join(
        f'<tr>'
        f'<td style="font-family:{_MONO};font-size:11px;letter-spacing:.06em;text-transform:uppercase;'
        f'color:#5c6e7e;padding:5px 14px 5px 0;vertical-align:top;white-space:nowrap;">{_esc(render_template(k, v))}</td>'
        f'<td style="font-family:{_FONT};font-size:14px;color:#15222e;padding:5px 0;font-weight:600;">{_esc(render_template(val, v))}</td>'
        f'</tr>'
        for k, val in rows
    )
    detail_block = (
        f'<tr><td style="padding:0 32px 20px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:#f4f7fa;border:1px solid #d3dde5;border-radius:3px;">'
        f'<tr><td style="padding:14px 18px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{details_html}</table>'
        f'</td></tr></table></td></tr>'
    ) if details_html else ""

    cta_label = render_template(template.cta_label, v) if template.cta_label else None
    cta_url = render_template(template.cta_url, v) if template.cta_url else None
    cta_block = ""
    if cta_label and cta_url:
        cta_block = (
            f'<tr><td style="padding:0 32px 22px;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="background:#10508c;border-radius:3px;">'
            f'<a href="{_esc(cta_url)}" style="display:inline-block;padding:13px 26px;font-family:{_FONT};'
            f'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">{_esc(cta_label)}</a>'
            f'</td></tr></table>'
            f'<p style="font-family:{_FONT};font-size:12px;color:#5c6e7e;margin:12px 0 0;word-break:break-all;">'
            f'If the button does not work, paste this into your browser:<br />'
            f'<a href="{_esc(cta_url)}" style="color:#10508c;">{_esc(cta_url)}</a></p>'
            f'</td></tr>'
        )

    status_block = ""
    if status_text:
        status_block = (
            f'<tr><td style="padding:13px 32px;background:{tone["bg"]};border-bottom:1px solid {tone["edge"]};">'
            f'<span style="font-family:{_MONO};font-size:11px;font-weight:600;letter-spacing:.13em;'
            f'text-transform:uppercase;color:{tone["ink"]};">{_esc(status_text)}</span></td></tr>'
        )

    footer_line = _esc(render_template(foot_text, v))
    footer_legal_html = f'<br /><br />{_esc(render_template(foot_legal, v))}' if foot_legal else ""

    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<meta name="x-apple-disable-message-reformatting" />
<title>{_esc(subject)}</title></head>
<body style="margin:0;padding:0;background:#eef2f6;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef2f6;">
<tr><td align="center" style="padding:24px 12px;">

  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
         style="width:600px;max-width:100%;background:#ffffff;border:1px solid #d3dde5;border-radius:4px;">

    <tr><td style="padding:20px 32px;border-bottom:1px solid #e4ebf0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="width:1%;white-space:nowrap;padding-right:11px;">
          <img src="{_esc(v.get("portal_url"))}/ankit-logo.png" alt="{_esc(v.get("company_name"))}" height="28" style="display:block;height:28px;width:auto;">
        </td>
        <td style="font-family:{_FONT};font-size:16px;font-weight:700;color:#15222e;">
          {_esc(v.get("company_name"))}
          <span style="font-family:{_MONO};font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#5c6e7e;">&nbsp; Supplier Portal</span>
        </td></tr></table>
    </td></tr>

    {status_block}

    <tr><td style="padding:28px 32px 6px;">
      <h1 style="font-family:{_FONT};font-size:25px;line-height:1.2;font-weight:700;letter-spacing:-.4px;
                 color:#15222e;margin:0 0 14px;">{_esc(heading)}</h1>
      {_paragraphs(template.intro, v)}
    </td></tr>

    {detail_block}
    {cta_block}

    <tr><td style="padding:0 32px 8px;">{_paragraphs(template.outro, v)}</td></tr>
  </table>

  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;">
    <tr><td style="padding:16px 32px 0;font-family:{_FONT};font-size:12px;line-height:1.6;color:#5c6e7e;">
      {footer_line}{footer_legal_html}
    </td></tr></table>

</td></tr></table></body></html>"""

    text_lines = [heading.upper()]
    if status_text:
        text_lines.append(status_text)
    text_lines += ["", render_template(template.intro, v) or "", ""]
    for k, val in rows:
        text_lines.append(f"{render_template(k, v)}: {render_template(val, v)}")
    if cta_label and cta_url:
        text_lines += ["", f"{cta_label.upper()}: {cta_url}"]
    text_lines += ["", render_template(template.outro, v) or "", "", "---", render_template(foot_text, v) or ""]
    if foot_legal:
        text_lines.append(render_template(foot_legal, v))
    text_body = "\n".join(text_lines)

    return subject, html_body, text_body


async def send_triggered_email(db: Session, mail_key: str, to_email: str, variables: dict) -> bool:
    """Look up an email_templates row by mail_key, render it, and send it.
    No-ops (logged) when the row is missing or disabled — an admin turning a
    mail off must silently stop it, not error the caller's request flow."""
    template = db.query(models.EmailTemplate).filter(models.EmailTemplate.mail_key == mail_key).first()
    if not template:
        logger.warning("send_triggered_email: no template for mail_key=%s", mail_key)
        return False
    if not template.enabled:
        logger.info("send_triggered_email: mail_key=%s is disabled — skipping", mail_key)
        return False
    if not to_email:
        logger.warning("send_triggered_email: no recipient for mail_key=%s", mail_key)
        return False

    subject, html_body, text_body = render_email_template(template, variables)
    return await notification_service.send_email(to=[to_email], subject=subject, html_body=html_body, text_body=text_body)
