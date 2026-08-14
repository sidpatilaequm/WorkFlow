"""
Helpers for resolving a request-stage's config for execution purposes
(who's an approver, what's the voting rule/SLA/labels) from the request's
frozen workflow_snapshot, instead of the live Workflow/WorkflowStage/
ApproverGroupMember tables.

This is what makes "old requests keep running under the config they were
submitted under; only new requests pick up admin edits" actually true for
execution — not just for display. get_stage_config() is the single place
every approval-flow function should go through instead of querying
models.WorkflowStage / models.ApproverGroupMember directly.

Live User rows are still consulted for things that are inherently real-time
(current OOO status, current delegate) — only the frozen facts (which
user_ids belong to a stage, voting rule, SLA hours, labels) come from the
snapshot.

Backward compatibility: requests submitted before this feature shipped have
workflow_snapshot = NULL. For those, get_stage_config() falls back to
building an equivalent dict from the live tables, so old in-flight requests
don't break on upgrade — they just don't get the "frozen" protection.
"""
from typing import Optional
from sqlalchemy.orm import Session
import models


def _live_stage_config(db: Session, stage_id: int) -> Optional[dict]:
    stage_def = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == stage_id).first()
    if not stage_def:
        return None
    group = stage_def.approver_group
    members = []
    if group:
        for m in sorted(group.members, key=lambda mm: mm.sequential_order):
            if m.user:
                members.append({
                    "user_id": m.user.id,
                    "name": m.user.name,
                    "email": m.user.email,
                    "is_optional": m.is_optional,
                    "sequential_order": m.sequential_order,
                })
    return {
        "stage_id": stage_def.id,
        "order": stage_def.order,
        "name": stage_def.name,
        "type": stage_def.type.value if stage_def.type else None,
        "sla_hours": stage_def.sla_hours,
        "voting_rule": stage_def.voting_rule.value if stage_def.voting_rule else None,
        "approve_label": stage_def.approve_label,
        "reject_label": stage_def.reject_label,
        "is_optional": stage_def.is_optional,
        "instructions": stage_def.instructions,
        "parallel_group": stage_def.parallel_group,
        "approver_group_id": stage_def.approver_group_id,
        "approver_group_name": group.name if group else None,
        "members": members,
    }


def get_stage_config(
    db: Session,
    req: "models.WorkflowRequest",
    stage_order: int,
    stage_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Returns a plain dict describing the stage as it should be enforced for
    this request: {stage_id, order, name, type, sla_hours, voting_rule,
    approve_label, reject_label, is_optional, instructions,
    approver_group_id, approver_group_name, members: [...]}.

    Prefers req.workflow_snapshot (frozen at submission). Falls back to the
    live tables via stage_id for pre-snapshot requests.
    """
    snap = req.workflow_snapshot
    if snap:
        for s in snap.get("stages", []):
            if s.get("order") == stage_order:
                return s
    if stage_id is None:
        return None
    return _live_stage_config(db, stage_id)


def member_ids(stage_cfg: Optional[dict]) -> set:
    if not stage_cfg:
        return set()
    return {m["user_id"] for m in stage_cfg.get("members", [])}


def optional_member_ids(stage_cfg: Optional[dict]) -> set:
    if not stage_cfg:
        return set()
    return {m["user_id"] for m in stage_cfg.get("members", []) if m.get("is_optional")}


def next_sequential_member(stage_cfg: Optional[dict], acted_user_ids) -> Optional[dict]:
    """Next not-yet-acted member by sequential_order, or None."""
    if not stage_cfg:
        return None
    members = sorted(stage_cfg.get("members", []), key=lambda m: m.get("sequential_order", 0))
    for m in members:
        if m["user_id"] not in acted_user_ids:
            return m
    return None