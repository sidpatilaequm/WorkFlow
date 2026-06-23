"""add approver_group_members.is_optional and workflow_requests.workflow_snapshot

Revision ID: 0001_optional_and_snapshot
Revises:
Create Date: 2026-06-19

This is the first Alembic migration for this project (Alembic was just set
up; the DB itself predates it). It captures the two model changes made
ahead of Alembic being wired in:

  - ApproverGroupMember.is_optional (Boolean) — already assumed by
    routers/stages.py and routers/approvals.py, but missing from the table.
  - WorkflowRequest.workflow_snapshot (JSON) — frozen stage/approver-group
    config captured at submission time (routers/requests.py:_build_workflow_snapshot).

Uses "IF NOT EXISTS" guards since one or both columns may already have been
added by hand in some environments before this migration existed.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_optional_and_snapshot"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.approver_group_members "
        f"ADD COLUMN IF NOT EXISTS is_optional BOOLEAN DEFAULT FALSE"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.workflow_requests "
        f"ADD COLUMN IF NOT EXISTS workflow_snapshot JSON NULL"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.workflow_requests "
        f"DROP COLUMN IF EXISTS workflow_snapshot"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.approver_group_members "
        f"DROP COLUMN IF EXISTS is_optional"
    )
