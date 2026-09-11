"""add table_blocks column to email_templates

Revision ID: 0010_email_template_table_blocks
Revises: 0009_report_schedules
Create Date: 2026-09-11

Admin-editable dynamic tables: unlike detail_rows (a fixed list of label/value pairs authored in
the editor), a table_blocks entry references a runtime variable name and renders one row per item
in whatever list the trigger caller puts under that key at send time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010_email_template_table_blocks"
down_revision: Union[str, None] = "0009_report_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    conn = op.get_bind()
    has_column = conn.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = :schema AND table_name = 'email_templates' AND column_name = 'table_blocks'"
    ), {"schema": SCHEMA}).scalar()
    if not has_column:
        op.execute(
            f"ALTER TABLE {SCHEMA}.email_templates "
            f"ADD COLUMN table_blocks JSON NULL "
            f"COMMENT 'Dynamic per-row tables — see models.py EmailTemplate.table_blocks'"
        )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.email_templates DROP COLUMN table_blocks")
