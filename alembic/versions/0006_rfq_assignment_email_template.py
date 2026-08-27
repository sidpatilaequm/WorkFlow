"""add PR.1 (RFQ assignment) email template

Revision ID: 0006_rfq_assignment_email_template
Revises: 0005_change_request_email_templates
Create Date: 2026-08-27

backend_java's PurchaseRequisitionServiceImpl used to build the "New RFQ:
Purchase Requisition Assigned" email inline (two duplicated call sites) via
the old plain-text EmailService, completely bypassing this admin-editable
template system — not previewable, not editable, not listed anywhere in the
admin panel. This adds the one seed row (mail_key "PR.1") those two call
sites now trigger through send_triggered_email/WorkflowEmailClient instead.

No workflows.email_process_key update here — unlike vendor_onboarding/
vendor_change_request, RFQ assignment isn't a WorkFlow-approval-driven
process (Java writes the PurchaseRequisition rows directly, no WorkflowRequest
involved), so there's no workflow row to tag. process_key is purely a UI
grouping label for the admin template list.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_rfq_assignment_email_template"
down_revision: Union[str, None] = "0005_change_request_email_templates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    conn = op.get_bind()

    existing_footer = conn.execute(
        sa.text(f"SELECT id FROM {SCHEMA}.email_footers WHERE name = :name"),
        {"name": "Supplier footer"},
    ).first()
    footer_id = existing_footer[0] if existing_footer else None

    import json

    template = {
        "process_key": "purchase_requisition",
        "mail_key": "PR.1",
        "mail_label": "RFQ assigned to vendor",
        "from_address": "Ankit Aerospace Private Limited <no-reply@nexdsupportal.in>",
        "reply_to": None,
        "status_strip_text": "Action needed · quotation requested",
        "status_strip_tone": "info",
        "subject": "New RFQ: Purchase Requisition Assigned — {{pr_number}}",
        "preheader": "You've been asked to quote on a new item — sign in to submit your price.",
        "heading": "You've been assigned a new RFQ",
        "intro": "Dear {{contact_name}},\n\nYou have been assigned to provide a quotation for a new item.",
        "detail_rows": json.dumps([
            ["PR number", "{{pr_number}}"],
            ["Item SKU", "{{item_sku}}"],
            ["Quantity", "{{quantity}}"],
        ]),
        "cta_label": "Sign in to submit your quotation",
        "cta_url": "{{portal_url}}",
        "outro": "Please log in to your vendor portal to submit your quotation.",
        "footer_id": footer_id,
        "sample_data": json.dumps({
            "contact_name": "Kite Polymers Pvt Ltd",
            "vendor_name": "Kite Polymers Pvt Ltd",
            "pr_number": "PR-2026-0142",
            "item_sku": "SKU-88213",
            "quantity": "500 PCS",
        }),
    }

    existing = conn.execute(
        sa.text(f"SELECT id FROM {SCHEMA}.email_templates WHERE mail_key = :mail_key"),
        {"mail_key": template["mail_key"]},
    ).first()
    if existing:
        return

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
        template,
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.email_templates WHERE mail_key = 'PR.1'")
