"""add parallel_group to workflow_stages and standalone_messages table

Revision ID: 0003_parallel_group_and_standalone_messages
Revises: 0002_message_variables_and_scheduled_messages
Create Date: 2026-06-19

Covers:
  - W1: workflow_stages.parallel_group (INT NULL) — stages sharing the same
        non-null integer within a workflow start simultaneously when a request
        is submitted / advances through the stage chain. NULL = serial.
  - M4: standalone_messages table — fire a notification to arbitrary email
        addresses without any workflow request context; supports {{key}}
        rendering and optional recurring resend.
"""
import sqlalchemy as sa
from alembic import op

SCHEMA = "multimedia_governance"


# ─── Upgrade ──────────────────────────────────────────────────────────────────

def upgrade():
    # 1. parallel_group column on workflow_stages
    op.execute(
        f"ALTER TABLE {SCHEMA}.workflow_stages "
        f"ADD COLUMN IF NOT EXISTS parallel_group INT NULL DEFAULT NULL "
        f"COMMENT 'Stages in the same workflow sharing a non-null parallel_group start simultaneously'"
    )

    # 2. standalone_messages table
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.standalone_messages (
            id                      INT            NOT NULL AUTO_INCREMENT,
            sender_id               INT            NULL,
            to_emails               JSON           NOT NULL
                COMMENT 'List of recipient email strings',
            subject                 VARCHAR(300)   NULL,
            message                 TEXT           NOT NULL,
            context                 JSON           NULL
                COMMENT 'Flat key-value dict for {{key}} placeholder rendering',
            reminder_interval_hours INT            NULL
                COMMENT 'Re-send every N hours when set; NULL = one-shot',
            max_reminders           INT            NULL
                COMMENT 'Cap on re-sends; NULL = unlimited until deactivated',
            reminders_sent          INT            NOT NULL DEFAULT 0,
            last_sent_at            DATETIME(6)    NULL,
            is_active               TINYINT(1)     NOT NULL DEFAULT 1,
            created_at              DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            PRIMARY KEY (id),
            CONSTRAINT fk_standalone_messages_sender
                FOREIGN KEY (sender_id)
                REFERENCES {SCHEMA}.user_details (userId)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


# ─── Downgrade ────────────────────────────────────────────────────────────────

def downgrade():
    op.execute(
        f"ALTER TABLE {SCHEMA}.workflow_stages "
        f"DROP COLUMN IF EXISTS parallel_group"
    )
    op.execute(
        f"DROP TABLE IF EXISTS {SCHEMA}.standalone_messages"
    )
