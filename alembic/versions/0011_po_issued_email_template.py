"""add PO.1 (PO issued) email template, with a dynamic line-items table

Revision ID: 0011_po_issued_email_template
Revises: 0010_email_template_table_blocks
Create Date: 2026-09-11

Today, creating a Purchase Order from an awarded quotation
(PortalPurchaseOrderServiceImpl.createPOFromAwardedQuotation) is a pure data
operation -- no email, no notification of any kind; a vendor only finds out
by logging into the portal. This adds mail_key "PO.1", triggered from that
same method, mirroring PR.1's pattern.

Unlike PR.1 (one item per RFQ assignment), a PO can have any number of line
items, so this is also the first template seeded with a table_blocks entry
(see 0010) instead of a fixed detail_rows list -- the row count comes from
however many items backend_java puts under the "line_items" variable at
send time.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_po_issued_email_template"
down_revision: Union[str, None] = "0010_email_template_table_blocks"
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
        "process_key": "purchase_order",
        "mail_key": "PO.1",
        "mail_label": "Purchase Order issued",
        "from_address": "Ankit Aerospace Private Limited <no-reply@nexdsupportal.in>",
        "reply_to": None,
        "status_strip_text": "Purchase Order issued",
        "status_strip_tone": "ok",
        "subject": "Purchase Order {{po_number}} issued",
        "preheader": "A new Purchase Order has been issued to you — sign in to view the details.",
        "heading": "Purchase Order {{po_number}}",
        "intro": "Dear {{vendor_name}},\n\nA new Purchase Order has been issued to you. The line items are below.",
        "detail_rows": json.dumps([
            ["PO number", "{{po_number}}"],
            ["PO date", "{{po_date}}"],
            ["Grand total", "{{grand_total}} {{currency}}"],
        ]),
        "table_blocks": json.dumps([
            {
                "list_variable": "line_items",
                "title": "Line items",
                "columns": [
                    {"header": "Item code", "value_template": "{{item_code}}"},
                    {"header": "Description", "value_template": "{{description}}"},
                    {"header": "Quantity", "value_template": "{{quantity}}"},
                    {"header": "Unit price", "value_template": "{{unit_price}}"},
                    {"header": "Net value", "value_template": "{{net_value}}"},
                ],
            }
        ]),
        "cta_label": "Sign in to view this Purchase Order",
        "cta_url": "{{portal_url}}",
        "outro": "Please log in to your vendor portal for the full Purchase Order details.",
        "footer_id": footer_id,
        "sample_data": json.dumps({
            "vendor_name": "Kite Polymers Pvt Ltd",
            "po_number": "PO-2026-0142",
            "po_date": "2026-09-11",
            "currency": "INR",
            "grand_total": "184500.00",
            "line_items": [
                {"item_code": "SKU-88213", "description": "Hex Bolt M8x40", "quantity": "500 PCS", "unit_price": "12.50", "net_value": "6250.00"},
                {"item_code": "SKU-91027", "description": "Washer M8", "quantity": "1000 PCS", "unit_price": "1.20", "net_value": "1200.00"},
            ],
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
                 detail_rows, table_blocks, cta_label, cta_url, outro, footer_id, sample_data)
            VALUES
                (:process_key, :mail_key, :mail_label, 1, :from_address, :reply_to,
                 :status_strip_text, :status_strip_tone, :subject, :preheader, :heading, :intro,
                 :detail_rows, :table_blocks, :cta_label, :cta_url, :outro, :footer_id, :sample_data)
        """),
        template,
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.email_templates WHERE mail_key = 'PO.1'")
