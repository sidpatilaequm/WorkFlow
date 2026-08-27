"""add email_template_revisions table

Revision ID: 0007_email_template_revisions
Revises: 0006_rfq_assignment_email_template
Create Date: 2026-08-27

Editing a live email_templates row previously overwrote it in place with no
history — this adds a snapshot table update_template writes to (before
applying each PATCH), so "what did this email say before" is answerable and
edits are attributable to who made them.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007_email_template_revisions"
down_revision: Union[str, None] = "0006_rfq_assignment_email_template"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.email_template_revisions (
            id              INT          NOT NULL AUTO_INCREMENT,
            template_id     INT          NOT NULL,
            snapshot        JSON         NOT NULL,
            changed_by_id   BIGINT       NULL,
            changed_at      DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            PRIMARY KEY (id),
            KEY idx_email_template_revisions_template_id (template_id),
            CONSTRAINT fk_email_template_revisions_template
                FOREIGN KEY (template_id) REFERENCES {SCHEMA}.email_templates (id)
                ON DELETE CASCADE,
            CONSTRAINT fk_email_template_revisions_changed_by
                FOREIGN KEY (changed_by_id) REFERENCES {SCHEMA}.user_details (user_id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.email_template_revisions")
