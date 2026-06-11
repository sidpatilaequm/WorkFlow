from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from database import get_db
from schemas import ApprovalActionCreate, ApprovalActionOut
from auth_utils import get_current_user
import models

router = APIRouter()


def _advance_request(db: Session, req: models.WorkflowRequest):
    """Move request to next stage or mark final status."""
    now = datetime.utcnow()
    next_stage = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.request_id == req.id,
            models.RequestStage.stage_order > req.current_stage,
            models.RequestStage.status == models.RequestStatus.pending
        )
        .order_by(models.RequestStage.stage_order)
        .first()
    )

    if next_stage:
        req.current_stage = next_stage.stage_order
        next_stage.started_at = now
        stage_def = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == next_stage.stage_id).first()
        if stage_def:
            next_stage.sla_deadline = now + timedelta(hours=stage_def.sla_hours)
    else:
        req.status = models.RequestStatus.approved
        req.resolved_at = now


def _check_stage_completion(db: Session, request_stage: models.RequestStage, req: models.WorkflowRequest):
    """Evaluate voting rule and decide if stage is complete."""
    stage_def = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == request_stage.stage_id).first()
    if not stage_def:
        return

    actions = request_stage.actions
    approved_count = sum(1 for a in actions if a.decision == models.ApprovalDecision.approved)
    rejected_count = sum(1 for a in actions if a.decision == models.ApprovalDecision.rejected)

    group_members = (
        db.query(models.ApproverGroupMember)
        .filter(models.ApproverGroupMember.group_id == stage_def.approver_group_id)
        .count()
    )

    now = datetime.utcnow()
    completed = False

    if stage_def.voting_rule == models.VotingRule.any:
        if approved_count >= 1:
            completed = True
            request_stage.status = models.RequestStatus.approved
    elif stage_def.voting_rule == models.VotingRule.all:
        if approved_count >= group_members:
            completed = True
            request_stage.status = models.RequestStatus.approved
    elif stage_def.voting_rule == models.VotingRule.sequential:
        # Sequential: last action decides
        if actions:
            last = sorted(actions, key=lambda a: a.acted_at)[-1]
            if last.decision == models.ApprovalDecision.approved:
                completed = True
                request_stage.status = models.RequestStatus.approved

    # Any rejection with stop behavior → reject entire request
    if rejected_count >= 1:
        workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
        behavior = workflow.rejection_behavior if workflow else models.RejectionBehavior.stop

        if behavior == models.RejectionBehavior.stop:
            request_stage.status = models.RequestStatus.rejected
            req.status = models.RequestStatus.rejected
            req.resolved_at = now
            return
        elif behavior == models.RejectionBehavior.restart:
            # Reset all stages and restart from beginning
            for rs in req.stages:
                rs.status = models.RequestStatus.pending
                rs.started_at = None
                rs.completed_at = None
            req.current_stage = -1
            _advance_request(db, req)
            return
        elif behavior == models.RejectionBehavior.escalate:
            request_stage.status = models.RequestStatus.escalated
            req.status = models.RequestStatus.escalated
            return

    if completed:
        request_stage.completed_at = now
        _advance_request(db, req)


@router.post("/", response_model=ApprovalActionOut)
def take_action(
    payload: ApprovalActionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == payload.request_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != models.RequestStatus.pending:
        raise HTTPException(400, f"Request is already {req.status.value}")

    # Find active stage - robust lookup
    active_stage = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.request_id == req.id,
            models.RequestStage.stage_order == req.current_stage,
            models.RequestStage.status == models.RequestStatus.pending
        )
        .first()
    )

    # Self-healing: if no exact match, find the first pending stage that has started
    if not active_stage:
        active_stage = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req.id,
                models.RequestStage.status == models.RequestStatus.pending,
                models.RequestStage.started_at.isnot(None)
            )
            .order_by(models.RequestStage.stage_order)
            .first()
        )
        # Sync current_stage if found
        if active_stage:
            req.current_stage = active_stage.stage_order
            db.flush()

    if not active_stage:
        raise HTTPException(400, "No active stage found for this request")

    stage_def = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == active_stage.stage_id).first()

    # Verify approver is in the group
    if current_user.role not in (models.UserRole.admin,):
        membership = (
            db.query(models.ApproverGroupMember)
            .filter(
                models.ApproverGroupMember.group_id == stage_def.approver_group_id,
                models.ApproverGroupMember.user_id == current_user.id
            )
            .first()
        )
        if not membership:
            raise HTTPException(403, "You are not in the approver group for this stage")

    # Prevent duplicate action
    existing = next((a for a in active_stage.actions if a.approver_id == current_user.id), None)
    if existing:
        raise HTTPException(400, "You have already acted on this stage")

    # Handle OOO delegation
    approver_id = current_user.id
    delegated_to = None
    if payload.decision == models.ApprovalDecision.delegated:
        if not payload.delegated_to_id:
            raise HTTPException(400, "delegated_to_id required for delegation")
        delegated_to = payload.delegated_to_id

    action = models.ApprovalAction(
        request_stage_id=active_stage.id,
        approver_id=approver_id,
        decision=payload.decision,
        comment=payload.comment,
        delegated_to_id=delegated_to,
    )
    db.add(action)
    db.flush()

    # Log activity
    verb = payload.decision.value
    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action=verb,
        detail=f"Stage {req.current_stage} {verb} by {current_user.name}. {payload.comment or ''}"
    ))

    _check_stage_completion(db, active_stage, req)
    db.commit()
    db.refresh(action)
    return action


@router.get("/pending")
def my_pending(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Return request IDs where the current user has a pending action."""
    group_ids = [
        m.group_id for m in
        db.query(models.ApproverGroupMember)
        .filter(models.ApproverGroupMember.user_id == current_user.id)
        .all()
    ]
    if not group_ids:
        return []

    pending_stages = (
        db.query(models.RequestStage)
        .join(models.WorkflowStage, models.RequestStage.stage_id == models.WorkflowStage.id)
        .filter(
            models.WorkflowStage.approver_group_id.in_(group_ids),
            models.RequestStage.status == models.RequestStatus.pending,
            models.RequestStage.started_at.isnot(None)
        )
        .all()
    )

    acted_stage_ids = [
        a.request_stage_id for a in
        db.query(models.ApprovalAction)
        .filter(models.ApprovalAction.approver_id == current_user.id)
        .all()
    ]

    result = [
        ps.request_id for ps in pending_stages
        if ps.id not in acted_stage_ids
    ]
    return list(set(result))