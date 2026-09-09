"""add report_schedules table

Revision ID: 0009_report_schedules
Revises: 0008_drop_organisation_add_department_company
Create Date: 2026-09-09

Recurring "email this report" scheduling for analytics (NexD Designer)
reports — config lives here (which published report, recipients, how
often), the actual render+send stays in the analytics service. See
services/report_schedules.py and models.ReportSchedule.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009_report_schedules"
down_revision: Union[str, None] = "0008_drop_organisation_add_department_company"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.report_schedules (
            id              INT          NOT NULL AUTO_INCREMENT,
            process_key     VARCHAR(64)  NOT NULL,
            token           VARCHAR(64)  NOT NULL,
            role            VARCHAR(32)  NOT NULL DEFAULT '',
            report_name     VARCHAR(190) NOT NULL DEFAULT '',
            recipients      JSON         NOT NULL,
            interval_hours  INT          NOT NULL DEFAULT 24,
            message         VARCHAR(500) NOT NULL DEFAULT '',
            is_active       TINYINT(1)   NOT NULL DEFAULT 1,
            last_sent_at    DATETIME     NULL,
            last_error      VARCHAR(500) NULL,
            created_by      INT          NULL,
            created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            CONSTRAINT fk_report_schedules_created_by
                FOREIGN KEY (created_by) REFERENCES {SCHEMA}.user_details (user_id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.report_schedules")
