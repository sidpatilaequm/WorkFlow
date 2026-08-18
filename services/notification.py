"""
Notification Service
Sends email and/or Slack messages at each workflow event.
Ported from workflow_engine with Vendors_Workflow model names.
"""
import hmac
import hashlib
import json
import logging
from typing import Optional

import httpx
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Workflow Engine")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


class NotificationService:

    # -------------------------------------------------------------------------
    # Email
    # -------------------------------------------------------------------------
    async def send_email(
        self,
        to: list[str],
        subject: str,
        html_body: str,
        text_body: str = "",
    ) -> bool:
        if not SMTP_USER or not SMTP_PASSWORD:
            logger.warning("SMTP not configured — skipping email")
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_USER}>"
            msg["To"] = ", ".join(to)
            if text_body:
                msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            await aiosmtplib.send(
                msg,
                hostname=SMTP_HOST,
                port=SMTP_PORT,
                username=SMTP_USER,
                password=SMTP_PASSWORD,
                start_tls=True,
            )
            logger.info("Email sent to %s: %s", to, subject)
            return True
        except Exception as exc:
            logger.error("Email send failed: %s", exc)
            return False

    # Maps stage type → (positive button label, negative button label, subject verb)
    STAGE_TYPE_LABELS = {
        "approval":        ("✓ Approve",        "✗ Reject",          "Approval"),
        "review":          ("✓ Mark Reviewed",  "✗ Request Changes", "Review"),
        "acknowledgement": ("✓ Acknowledge",    "✗ Decline",         "Acknowledgement"),
        "signature":       ("✓ Sign",           "✗ Refuse",          "Signature"),
    }

    async def notify_approvers(
        self,
        approver_emails: list[str],
        request,  # models.WorkflowRequest
        stage_name: str,
        workflow_name: str,
        approve_token: str,
        reject_token: str,
        stage_type: str = "approval",
        approve_label: str = None,
        reject_label: str = None,
        note: str = None,
        instructions: str = None,
    ) -> None:
        approve_url = f"{FRONTEND_URL}/action/{approve_token}"
        reject_url = f"{FRONTEND_URL}/action/{reject_token}"

        positive_label, negative_label, subject_verb = self.STAGE_TYPE_LABELS.get(
            stage_type, self.STAGE_TYPE_LABELS["approval"]
        )
        # Per-stage free-text overrides win over the type preset.
        positive_label = approve_label or positive_label
        negative_label = reject_label or negative_label

        instructions_section = ""
        if instructions:
            instructions_section = f"""
            <div style="margin:16px 0;padding:12px;background:#f9f9f9;border-left:4px solid #4F7DFF">
              <p style="margin:0;font-weight:bold;font-size:13px">Instructions:</p>
              <p style="margin:4px 0 0;font-size:14px;color:#444">{instructions}</p>
            </div>
            """

        for email in approver_emails:
            # Isolate each recipient — a failure for one must never abort the rest
            try:
                amount_row = (
                    f'<tr><td style="padding:8px;border:1px solid #eee;font-weight:bold">Amount</td>'
                    f'<td style="padding:8px;border:1px solid #eee">&#8377;{request.amount:,.2f}</td></tr>'
                    if request.amount else ""
                )
                note_html = (
                    f'<p style="background:#FFF7E0;padding:10px 14px;border-radius:6px;color:#7A5B00;font-size:13px">{note}</p>'
                    if note else ""
                )
                html = f"""
                <div style="font-family:sans-serif;max-width:600px;margin:0 auto">
                  <h2>{subject_verb} required: {workflow_name}</h2>
                  {note_html}
                  <p>A document is awaiting your {stage_name.lower()}:</p>
                  <table style="border-collapse:collapse;width:100%">
                    <tr><td style="padding:8px;border:1px solid #eee;font-weight:bold">Document</td>
                        <td style="padding:8px;border:1px solid #eee">{request.document_name or request.title}</td></tr>
                    <tr><td style="padding:8px;border:1px solid #eee;font-weight:bold">Stage</td>
                        <td style="padding:8px;border:1px solid #eee">{stage_name}</td></tr>
                    <tr><td style="padding:8px;border:1px solid #eee;font-weight:bold">Type</td>
                        <td style="padding:8px;border:1px solid #eee;text-transform:capitalize">{stage_type}</td></tr>
                    {amount_row}
                  </table>
                  {instructions_section}
                  <div style="margin:24px 0">
                    <a href="{approve_url}" style="background:#1D9E75;color:white;padding:12px 24px;text-decoration:none;border-radius:6px">{positive_label}</a>
                    &nbsp;&nbsp;
                    <a href="{reject_url}" style="background:#E24B4A;color:white;padding:12px 24px;text-decoration:none;border-radius:6px">{negative_label}</a>
                  </div>
                  <p style="color:#888;font-size:12px">Or review in full: <a href="{FRONTEND_URL}/requests/{request.id}">{FRONTEND_URL}/requests/{request.id}</a></p>
                </div>
                """
                await self.send_email(
                    to=[email],
                    subject=f"[{subject_verb} Required] {request.document_name or request.title} — {stage_name}",
                    html_body=html,
                    text_body=f"{positive_label}: {approve_url}\n{negative_label}: {reject_url}",
                )
            except Exception as exc:
                logger.error("notify_approvers: failed for %s — %s", email, exc)

    async def notify_submitter_completed(
        self,
        submitter_email: str,
        request,
        workflow_name: str,
        comments: list = None,
    ) -> None:
        from models import RequestStatus
        status_word = "approved" if request.status in (
            RequestStatus.approved,
        ) else "rejected"
        colour = "#1D9E75" if status_word == "approved" else "#E24B4A"

        from .email_builder import build_email_html
        
        comments_text = ""
        if comments:
            comments_text = "Decision Trail & Comments:\n"
            for c in comments:
                comments_text += f"- {c['stage']} ({c['actor']}): {c['comment']}\n"

        html = build_email_html(
            subject=f"[{status_word.upper()}] {request.document_name or request.title}",
            preheader=f"Your document has been {status_word}.",
            heading=f"Document {status_word.title()}",
            intro=f"Your document {request.document_name or request.title} has been {status_word} in the {workflow_name} workflow.",
            outro=comments_text,
            status=status_word.title(),
            tone="ok" if status_word == "approved" else "bad",
            cta="View Request",
            cta_url=f"{FRONTEND_URL}/requests?request={request.id}",
        )

        await self.send_email(
            to=[submitter_email],
            subject=f"[{status_word.upper()}] {request.document_name or request.title}",
            html_body=html,
        )

    # -------------------------------------------------------------------------
    # Standalone Messages (Messaging #4)
    # -------------------------------------------------------------------------
    async def send_standalone_message(
        self,
        to: list,
        subject: str,
        message: str,
        sender_name: str,
    ) -> bool:
        """Send a standalone email notification with no workflow-request context."""
        from .email_builder import build_email_html
        html = build_email_html(
            subject=subject,
            preheader=f"Message from {sender_name}",
            heading=subject,
            intro=message,
            outro=f"From {sender_name}",
            tone="info",
        )
        return await self.send_email(
            to=to,
            subject=subject,
            html_body=html,
            text_body=message,
        )

    # -------------------------------------------------------------------------
    # Ad-hoc messages (no stage / no SLA attached)
    # -------------------------------------------------------------------------
    async def notify_custom_message(
        self,
        to: list[str],
        request,  # models.WorkflowRequest
        message: str,
        sender_name: str,
        subject: Optional[str] = None,
    ) -> bool:
        from .email_builder import build_email_html
        
        email_subj = subject or f"[Message] {request.document_name or request.title}"
        
        html = build_email_html(
            subject=email_subj,
            preheader=f"Message about: {request.document_name or request.title}",
            heading=f"Message about: {request.document_name or request.title}",
            intro=message,
            outro=f"From {sender_name} · Request #{request.id}",
            tone="info",
            cta="View Request",
            cta_url=f"{FRONTEND_URL}/requests/{request.id}",
        )
        
        return await self.send_email(
            to=to,
            subject=email_subj,
            html_body=html,
            text_body=message,
        )

    # -------------------------------------------------------------------------
    # Slack
    # -------------------------------------------------------------------------
    async def send_slack(self, channel: str, text: str, blocks: list = None) -> bool:
        if not SLACK_BOT_TOKEN:
            logger.warning("Slack not configured — skipping")
            return False
        payload = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    json=payload,
                    headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
                    timeout=10,
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.error("Slack error: %s", data.get("error"))
                    return False
                return True
            except Exception as exc:
                logger.error("Slack send failed: %s", exc)
                return False

    async def notify_slack_approver(
        self,
        slack_user_id: str,
        request,
        stage_name: str,
        workflow_name: str,
    ) -> None:
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":page_facing_up: *Action Required: {workflow_name}*\n"
                        f"Document: *{request.document_name or request.title}*\n"
                        f"Stage: {stage_name}"
                        + (f"\nAmount: \u20b9{request.amount:,.2f}" if request.amount else "")
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "value": f"approve:{request.id}:{request.current_stage}",
                        "action_id": "wf_approve",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "value": f"reject:{request.id}:{request.current_stage}",
                        "action_id": "wf_reject",
                    },
                ],
            },
        ]
        await self.send_slack(
            channel=slack_user_id,
            text=f"Action required on {request.document_name or request.title}",
            blocks=blocks,
        )

    # -------------------------------------------------------------------------
    # Outgoing Webhooks
    # -------------------------------------------------------------------------
    async def fire_outgoing_webhook(self, url: str, payload: dict, secret: Optional[str] = None) -> bool:
        body = json.dumps(payload).encode()
        sig = ""
        effective_secret = secret or WEBHOOK_SECRET
        if effective_secret:
            sig = "sha256=" + hmac.new(
                effective_secret.encode(), body, hashlib.sha256
            ).hexdigest()

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Workflow-Signature": sig,
                        "X-Workflow-Event": payload.get("event", ""),
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                return True
            except Exception as exc:
                logger.error("Webhook to %s failed: %s", url, exc)
                return False


notification_service = NotificationService()
