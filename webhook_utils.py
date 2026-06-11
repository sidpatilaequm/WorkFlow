"""
Webhook dispatcher — call fire_webhook() after any significant state change.
Payloads follow a standard envelope so consumers (Slack, email, etc.) 
can handle all event types uniformly.
"""

import hashlib
import hmac
import json
import time
import httpx
from sqlalchemy.orm import Session
import models


EVENTS = {
    "request.submitted":  "A new workflow request was submitted",
    "stage.approved":     "A workflow stage was approved",
    "stage.rejected":     "A workflow stage was rejected",
    "stage.escalated":    "A stage was escalated due to SLA breach",
    "request.approved":   "A workflow request was fully approved",
    "request.rejected":   "A workflow request was rejected",
    "request.cancelled":  "A workflow request was cancelled",
}


def _sign_payload(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def build_payload(event: str, request: models.WorkflowRequest, extra: dict = None) -> dict:
    payload = {
        "event": event,
        "timestamp": int(time.time()),
        "request": {
            "id": request.id,
            "title": request.title,
            "status": request.status.value,
            "current_stage": request.current_stage,
            "workflow_id": request.workflow_id,
            "submitted_at": request.submitted_at.isoformat(),
            "document_name": request.document_name,
            "amount": request.amount,
            "department": request.department,
        },
        **(extra or {})
    }
    return payload


async def fire_webhook(db: Session, event: str, request: models.WorkflowRequest, extra: dict = None):
    """Find all active webhook configs for this event and fire them."""
    configs = (
        db.query(models.WebhookConfig)
        .filter(
            models.WebhookConfig.is_active == True,
            models.WebhookConfig.event == event,
        )
        .all()
    )
    # Also match workflow-specific configs
    workflow_configs = (
        db.query(models.WebhookConfig)
        .filter(
            models.WebhookConfig.is_active == True,
            models.WebhookConfig.event == event,
            models.WebhookConfig.workflow_id == request.workflow_id,
        )
        .all()
    )

    all_configs = {c.id: c for c in configs + workflow_configs}.values()

    payload = build_payload(event, request, extra)
    body = json.dumps(payload).encode()

    async with httpx.AsyncClient(timeout=5) as client:
        for cfg in all_configs:
            headers = {"Content-Type": "application/json"}
            if cfg.secret:
                headers["X-Signature-256"] = f"sha256={_sign_payload(cfg.secret, body)}"
            try:
                await client.post(cfg.url, content=body, headers=headers)
            except Exception as e:
                print(f"[webhook] Failed to fire {event} to {cfg.url}: {e}")


# ── Slack notification builder ───────────────────────────────────────────────

def slack_blocks(event: str, request: models.WorkflowRequest) -> dict:
    """Build a Slack Block Kit payload for a workflow event."""
    emoji = {
        "request.submitted": "📨",
        "stage.approved": "✅",
        "stage.rejected": "❌",
        "request.approved": "🎉",
        "request.rejected": "🚫",
        "stage.escalated": "⚠️",
        "request.cancelled": "🚫",
    }.get(event, "🔔")

    return {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{emoji} {EVENTS.get(event, event)}"}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Request:*\n{request.title}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{request.status.value.upper()}"},
                    {"type": "mrkdwn", "text": f"*Stage:*\n{request.current_stage + 1}"},
                    {"type": "mrkdwn", "text": f"*Amount:*\n{'₹' + str(request.amount) if request.amount else '—'}"},
                ]
            }
        ]
    }