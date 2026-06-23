"""add workflows.message_variables and scheduled_messages table

Revision ID: 0002_message_variables_and_scheduled_messages
Revises: 0001_optional_and_snapshot
Create Date: 2026-06-19

Captures the model changes added for the messaging-system work
(Messaging #2 ad-hoc message SLA/frequency, Messaging #3 / Workflow #9
template & derived-variable engine):

  - Workflow.message_variables (JSON) — derived/templated values available
    to message templates, evaluated by template_utils.resolve_template_variables.
  - ScheduledMessage table — recurring ad-hoc messages created via
    POST /requests/{id}/send-message when reminder_interval_hours is
    supplied, re-sent by services/escalation.py:send_message_reminders.

Uses "IF NOT EXISTS" guards consistent with 0001, since message_variables
may already have been added by hand in some environments.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_message_variables_and_scheduled_messages"
down_revision: Union[str, None] = "0001_optional_and_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.workflows "
        f"ADD COLUMN IF NOT EXISTS message_variables JSON NULL"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.scheduled_messages (
            id                       INTEGER NOT NULL AUTO_INCREMENT,
            request_id               INTEGER NULL,
            sender_id                INTEGER NULL,
            `to`                     VARCHAR(50) NOT NULL,
            custom_emails            JSON NULL,
            subject                  VARCHAR(300) NULL,
            message                  TEXT NOT NULL,
            reminder_interval_hours  INTEGER NOT NULL,
            max_reminders            INTEGER NULL,
            reminders_sent           INTEGER DEFAULT 0,
            last_sent_at             DATETIME NULL,
            is_active                BOOLEAN DEFAULT TRUE,
            created_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY ix_scheduled_messages_id (id),
            CONSTRAINT fk_scheduled_messages_request
                FOREIGN KEY (request_id) REFERENCES {SCHEMA}.workflow_requests (id)
                ON DELETE CASCADE,
            CONSTRAINT fk_scheduled_messages_sender
                FOREIGN KEY (sender_id) REFERENCES {SCHEMA}.user_details (userId)
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.scheduled_messages")
    op.execute(
        f"ALTER TABLE {SCHEMA}.workflows "
        f"DROP COLUMN IF EXISTS message_variables"
    )
