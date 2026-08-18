"""add email_footers/email_templates tables and workflows.email_process_key

Revision ID: 0004_email_templates
Revises: 0003_parallel_group_and_standalone_messages
Create Date: 2026-08-18

Backs the admin-editable "Workflow Email Templates" feature that replaces
backend_java's plain-text JavaMailSender emails (resume-code, credentials)
and WorkFlow's own dead vendor-specific branches in
notify_submitter_completed with a single, admin-editable HTML sender.

  - email_footers: shared footer library (a handful of rows; most templates
    point at one instead of carrying their own wording).
  - email_templates: one row per admin-editable transactional mail, keyed by
    a stable mail_key ("VO.2" etc) that trigger call sites reference.
  - workflows.email_process_key: stable marker so code can recognise "this
    is the Vendor Onboarding workflow" without matching on the (renameable)
    workflow name.

Only 3 mail_key rows are seeded (VO.2 draft-saved, VO.4 application-received,
VO.6 supplier-approved) — the only ones that map to real, live functionality
today. The schema supports more without changes; add rows later alongside
whatever feature each additional mail depends on.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_email_templates"
down_revision: Union[str, None] = "0003_parallel_group_and_standalone_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    # MySQL (unlike MariaDB) has no "ADD COLUMN IF NOT EXISTS" — check first.
    conn = op.get_bind()
    has_column = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = :schema AND table_name = 'workflows' AND column_name = 'email_process_key'"
    ), {"schema": SCHEMA}).scalar()
    if not has_column:
        op.execute(
            f"ALTER TABLE {SCHEMA}.workflows "
            f"ADD COLUMN email_process_key VARCHAR(50) NULL DEFAULT NULL "
            f"COMMENT 'Stable marker for code, e.g. vendor_onboarding — independent of the renameable workflow name'"
        )

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.email_footers (
            id           INT           NOT NULL AUTO_INCREMENT,
            name         VARCHAR(200)  NOT NULL,
            reason_text  TEXT          NOT NULL,
            legal_line   TEXT          NULL,
            created_at   DATETIME(6)   NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at   DATETIME(6)   NULL,
            PRIMARY KEY (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.email_templates (
            id                      INT            NOT NULL AUTO_INCREMENT,
            process_key             VARCHAR(50)    NOT NULL,
            mail_key                VARCHAR(20)    NOT NULL,
            mail_label              VARCHAR(200)   NOT NULL,
            enabled                 TINYINT(1)     NOT NULL DEFAULT 1,
            from_address            VARCHAR(300)   NULL,
            reply_to                VARCHAR(300)   NULL,
            status_strip_text       VARCHAR(300)   NULL,
            status_strip_tone       VARCHAR(10)    NULL,
            subject                 VARCHAR(300)   NOT NULL,
            preheader               VARCHAR(300)   NULL,
            heading                 VARCHAR(300)   NOT NULL,
            intro                   TEXT           NULL,
            detail_rows             JSON           NULL,
            cta_label               VARCHAR(100)   NULL,
            cta_url                 VARCHAR(500)   NULL,
            outro                   TEXT           NULL,
            footer_id               INT            NULL,
            footer_override_reason  TEXT           NULL,
            footer_override_legal   TEXT           NULL,
            sample_data             JSON           NULL,
            updated_by_id           BIGINT         NULL,
            created_at              DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at              DATETIME(6)    NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_email_templates_mail_key (mail_key),
            CONSTRAINT fk_email_templates_footer
                FOREIGN KEY (footer_id) REFERENCES {SCHEMA}.email_footers (id)
                ON DELETE SET NULL,
            CONSTRAINT fk_email_templates_updated_by
                FOREIGN KEY (updated_by_id) REFERENCES {SCHEMA}.user_details (user_id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    conn = op.get_bind()

    existing_footer = conn.execute(
        sa.text(f"SELECT id FROM {SCHEMA}.email_footers WHERE name = :name"),
        {"name": "Supplier footer"},
    ).first()
    if existing_footer:
        footer_id = existing_footer[0]
    else:
        conn.execute(
            sa.text(f"""
                INSERT INTO {SCHEMA}.email_footers (name, reason_text, legal_line)
                VALUES (:name, :reason_text, :legal_line)
            """),
            {
                "name": "Supplier footer",
                "reason_text": "You are receiving this because {{vendor_name}} is registered as a supplier with {{company_name}}.",
                "legal_line": "{{company_name}} · {{portal_url}}",
            },
        )
        footer_id = conn.execute(
            sa.text(f"SELECT id FROM {SCHEMA}.email_footers WHERE name = :name"),
            {"name": "Supplier footer"},
        ).first()[0]

    templates = [
        {
            "process_key": "vendor_onboarding",
            "mail_key": "VO.2",
            "mail_label": "Application saved as a draft",
            "from_address": "Ankt Aerospace Private Limited <no-reply@nexdmdg.com>",
            "reply_to": None,
            "status_strip_text": "Draft · not yet submitted",
            "status_strip_tone": "warn",
            "subject": "Your supplier registration draft is saved",
            "preheader": "Nothing has been submitted yet — enter your resume code to pick up where you left off.",
            "heading": "Your application is saved",
            "intro": "Hello {{contact_name}},\n\nWe've saved your progress on the supplier registration for {{vendor_name}}. Nothing has been sent to us yet — the review only starts once you submit it.",
            "detail_rows": [
                ["Resume code", "{{resume_code}}"],
                ["Still needed before you can submit", "{{missing_items}}"],
            ],
            "cta_label": "Resume your application",
            "cta_url": "{{portal_url}}/become-a-supplier",
            "outro": "Enter your resume code on the Become a Supplier page to pick up exactly where you left off.",
        },
        {
            "process_key": "vendor_onboarding",
            "mail_key": "VO.4",
            "mail_label": "Application received",
            "from_address": "Ankt Aerospace Private Limited <no-reply@nexdmdg.com>",
            "reply_to": None,
            "status_strip_text": "Submitted · under review",
            "status_strip_tone": "ok",
            "subject": "We've received your supplier application — {{vendor_name}}",
            "preheader": "Our procurement team will come back to you once a decision has been made.",
            "heading": "Your application is with us",
            "intro": "Hello {{contact_name}},\n\nThank you. Your supplier registration for {{vendor_name}} has been submitted and is now under review by our procurement team.",
            "detail_rows": [
                ["Applicant", "{{vendor_name}}"],
                ["Submitted by", "{{contact_name}}"],
            ],
            "cta_label": None,
            "cta_url": None,
            "outro": "You'll hear from us once a decision has been made. No further action is needed for now.",
        },
        {
            "process_key": "vendor_onboarding",
            "mail_key": "VO.6",
            "mail_label": "Supplier approved",
            "from_address": "Ankt Aerospace Private Limited <no-reply@nexdmdg.com>",
            "reply_to": None,
            "status_strip_text": "Approved · you are now an active supplier",
            "status_strip_tone": "ok",
            "subject": "{{vendor_name}} is now an approved supplier",
            "preheader": "Your vendor portal login has been created.",
            "heading": "You are approved",
            "intro": "Hello {{contact_name}},\n\nCongratulations — {{vendor_name}} has been approved and added to our supplier list. Your vendor portal login has been created below.",
            "detail_rows": [
                ["Vendor code", "{{vendor_code}}"],
                ["Login email", "{{login_email}}"],
                ["Password", "{{password}}"],
            ],
            "cta_label": "Sign in to the portal",
            "cta_url": "{{portal_url}}/login",
            "outro": "For security, please change your password immediately after your first login.",
        },
    ]

    sample_data = {
        "contact_name": "Ravi Shah", "vendor_name": "Kite Polymers Pvt Ltd",
        "resume_code": "8F21C4A9", "missing_items": "Cancelled cheque, ISO certificate",
        "vendor_code": "VEND-8F21C4A9", "login_email": "ravi.shah@kitepolymers.example",
        "password": "Xk4#mQ9wLp",
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
        sa.text(f"UPDATE {SCHEMA}.workflows SET email_process_key = 'vendor_onboarding' WHERE id = 8")
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.email_templates")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.email_footers")
    op.execute(
        f"ALTER TABLE {SCHEMA}.workflows "
        f"DROP COLUMN IF EXISTS email_process_key"
    )
