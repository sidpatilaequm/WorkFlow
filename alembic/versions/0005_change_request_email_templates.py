"""seed VCR.1/VCR.2 email templates and workflows.email_process_key='vendor_change_request'

Revision ID: 0005_change_request_email_templates
Revises: 0004_email_templates
Create Date: 2026-08-19

Adds the two admin-editable emails for the vendor self-service change-request
feature, in the same email_templates table 0004 introduced:

  - VCR.1 "Change request submitted" — sent when a vendor submits a request
    to change an already-approved document/attachment/answer.
  - VCR.2 "Change request decided" — sent once that request is approved or
    rejected; a single template covers both outcomes via the {{decision}}/
    {{decision_detail}} merge tags, since status_strip_tone can't vary
    per-send with the current schema (a fixed enum column per template row).

Also marks workflow id 13 ("Vendor Change Request") with its own
email_process_key so routers/approvals.py's _fire_completion_notification can
skip its generic fallback notice the same way it already does for vendor
onboarding (workflow id 8) — VCR.2, triggered from backend_java once the
change is actually applied/discarded, replaces it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_change_request_email_templates"
down_revision: Union[str, None] = "0004_email_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    conn = op.get_bind()

    footer_row = conn.execute(
        sa.text(f"SELECT id FROM {SCHEMA}.email_footers WHERE name = :name"),
        {"name": "Supplier footer"},
    ).first()
    footer_id = footer_row[0] if footer_row else None

    templates = [
        {
            "process_key": "vendor_change_request",
            "mail_key": "VCR.1",
            "mail_label": "Change request submitted",
            "from_address": "Ankit Aerospace Private Limited <no-reply@nexdsupportal.in>",
            "reply_to": None,
            "status_strip_text": "Submitted · under review",
            "status_strip_tone": "info",
            "subject": "We've received your change request — {{item_label}}",
            "preheader": "Your request is now with our approval team.",
            "heading": "Your change request is with us",
            "intro": "Hello {{contact_name}},\n\nWe've received your request to change {{item_label}} for {{vendor_name}}. It's now with the same team that reviewed your original application.",
            "detail_rows": [
                ["Item", "{{item_label}}"],
                ["Reason you gave", "{{reason}}"],
            ],
            "cta_label": None,
            "cta_url": None,
            "outro": "You'll hear from us once a decision has been made. No further action is needed for now.",
        },
        {
            "process_key": "vendor_change_request",
            "mail_key": "VCR.2",
            "mail_label": "Change request decided",
            "from_address": "Ankit Aerospace Private Limited <no-reply@nexdsupportal.in>",
            "reply_to": None,
            "status_strip_text": "{{decision}}",
            "status_strip_tone": "info",
            "subject": "Your change request has been {{decision}} — {{item_label}}",
            "preheader": "A decision has been made on your change request.",
            "heading": "A decision has been made",
            "intro": "Hello {{contact_name}},\n\nYour request to change {{item_label}} for {{vendor_name}} has been {{decision}}. {{decision_detail}}",
            "detail_rows": [
                ["Item", "{{item_label}}"],
                ["Decision", "{{decision}}"],
            ],
            "cta_label": "Sign in to the portal",
            "cta_url": "{{portal_url}}/login",
            "outro": "If you have questions about this decision, reach out to our procurement team.",
        },
    ]

    sample_data = {
        "contact_name": "Ravi Shah", "vendor_name": "Kite Polymers Pvt Ltd",
        "item_label": "GST registration certificate", "reason": "Certificate was reissued after an address update.",
        "decision": "approved", "decision_detail": "The updated document is now on file.",
    }

    import json

    for t in templates:
        existing = conn.execute(
            sa.text(f"SELECT id FROM {SCHEMA}.email_templates WHERE mail_key = :mail_key"),
            {"mail_key": t["mail_key"]},
        ).first()
        if existing:
            continue
        conn.execute(
            sa.text(f"""
                INSERT INTO {SCHEMA}.email_templates
                    (process_key, mail_key, mail_label, enabled, from_address, reply_to,
                     status_strip_text, status_strip_tone, subject, preheader, heading, intro,
                     detail_rows, cta_label, cta_url, outro, footer_id, sample_data)
                VALUES
                    (:process_key, :mail_key, :mail_label, 1, :from_address, :reply_to,
                     :status_strip_text, :status_strip_tone, :subject, :preheader, :heading, :intro,
                     :detail_rows, :cta_label, :cta_url, :outro, :footer_id, :sample_data)
            """),
            {
                **t,
                "detail_rows": json.dumps(t["detail_rows"]),
                "footer_id": footer_id,
                "sample_data": json.dumps(sample_data),
            },
        )

    conn.execute(
        sa.text(f"UPDATE {SCHEMA}.workflows SET email_process_key = 'vendor_change_request' WHERE id = 13")
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.email_templates WHERE mail_key IN ('VCR.1', 'VCR.2')")
    op.execute(
        f"UPDATE {SCHEMA}.workflows SET email_process_key = NULL WHERE id = 13 AND email_process_key = 'vendor_change_request'"
    )
