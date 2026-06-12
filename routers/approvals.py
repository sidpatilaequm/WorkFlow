from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from database import get_db
from schemas import ApprovalActionCreate, ApprovalActionOut
from auth_utils import create_approval_token, resolve_approver
from services.notification import notification_service
import asyncio
import threading
import models


def _run_async(coro):
    """Fire a coroutine from a sync context without conflicting with uvicorn's loop."""
    def _target():
        asyncio.run(coro)
    t = threading.Thread(target=_target, daemon=True)
    t.start()

router = APIRouter()


def _advance_request(db: Session, req: models.WorkflowRequest):
    """Move request to next stage or mark final status."""
    now = datetime.utcnow()
    
    while True:
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

        if not next_stage:
            req.status = models.RequestStatus.approved
            req.resolved_at = now
            # Notify submitter of final approval
            _fire_completion_notification(db, req)
            return

        stage_def = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == next_stage.stage_id).first()
        
        # Check if we should skip this stage (is_optional and no members)
        if stage_def and stage_def.is_optional:
            member_count = db.query(models.ApproverGroupMember).filter(
                models.ApproverGroupMember.group_id == stage_def.approver_group_id
            ).count()
            if member_count == 0:
                next_stage.status = models.RequestStatus.approved # Mark as skipped/auto-approved
                next_stage.completed_at = now
                req.current_stage = next_stage.stage_order
                db.add(models.ActivityLog(
                    request_id=req.id,
                    action="skipped",
                    detail=f"Optional stage '{stage_def.name}' skipped (no members)",
                    stage_order=next_stage.stage_order
                ))
                continue # Try next stage

        req.current_stage = next_stage.stage_order
        next_stage.started_at = now
        if stage_def:
            next_stage.sla_deadline = now + timedelta(hours=stage_def.sla_hours)
        # Notify approvers for the new stage
        _fire_stage_notification(db, req, next_stage, stage_def)
        return


def _fire_stage_notification(db, req, request_stage, stage_def):
    """Send email/Slack to approvers for a newly started stage."""
    if not stage_def:
        return
    workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
    if not workflow:
        return
    channel = workflow.notification_channel

    # Handle sequential voting: only notify the first/next member
    if stage_def.voting_rule == models.VotingRule.sequential:
        # Find which members have already acted
        acted_user_ids = [
            a.approver_id for a in
            db.query(models.ApprovalAction)
            .filter(models.ApprovalAction.request_stage_id == request_stage.id)
            .all()
        ]
        # Find the next member in sequence
        next_member = (
            db.query(models.ApproverGroupMember)
            .filter(
                models.ApproverGroupMember.group_id == stage_def.approver_group_id,
                models.ApproverGroupMember.user_id.notin_(acted_user_ids)
            )
            .order_by(models.ApproverGroupMember.sequential_order)
            .first()
        )
        if not next_member:
            return
        members = [next_member]
    else:
        # All members for Any/All
        members = (
            db.query(models.ApproverGroupMember)
            .filter(models.ApproverGroupMember.group_id == stage_def.approver_group_id)
            .all()
        )

    approver_emails = []
    for m in members:
        user = db.query(models.User).filter(models.User.id == m.user_id).first()
        if user:
            # Resolve OOO delegate
            actual_approver = resolve_approver(user, db)
            if actual_approver and actual_approver.email:
                approver_emails.append((actual_approver.email, actual_approver.id))
    
    if not approver_emails:
        return

    if channel in (models.NotificationChannel.email, models.NotificationChannel.both):
        for email, user_id in approver_emails:
            approve_token = create_approval_token(req.id, request_stage.stage_order, "approved", user_id)
            reject_token = create_approval_token(req.id, request_stage.stage_order, "rejected", user_id)
            _run_async(
                notification_service.notify_approvers(
                    approver_emails=[email],
                    request=req,
                    stage_name=stage_def.name,
                    workflow_name=workflow.name,
                    approve_token=approve_token,
                    reject_token=reject_token,
                    stage_type=stage_def.type.value if stage_def.type else "approval",
                    instructions=stage_def.instructions,
                )
            )


def _fire_completion_notification(db, req):
    """Notify submitter when request is fully approved or rejected."""
    workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
    submitter = db.query(models.User).filter(models.User.id == req.submitter_id).first()
    if not submitter or not submitter.email or not workflow:
        return

    # Collect all comments from approval actions
    comments = []
    for rs in req.stages:
        stage_name = rs.stage.name if rs.stage else f"Stage {rs.stage_order}"
        for action in rs.actions:
            if action.comment:
                approver_name = action.approver.name if action.approver else "Approver"
                comments.append({
                    "stage": stage_name,
                    "actor": approver_name,
                    "decision": action.decision.value,
                    "comment": action.comment
                })

    channel = workflow.notification_channel if workflow else models.NotificationChannel.email
    if channel in (models.NotificationChannel.email, models.NotificationChannel.both):
        _run_async(
            notification_service.notify_submitter_completed(
                submitter_email=submitter.email,
                request=req,
                workflow_name=workflow.name,
                comments=comments
            )
        )


def _check_stage_completion(db: Session, request_stage: models.RequestStage, req: models.WorkflowRequest):
    """Evaluate voting rule and decide if stage is complete."""
    stage_def = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == request_stage.stage_id).first()
    if not stage_def:
        return

    actions = (
        db.query(models.ApprovalAction)
        .filter(models.ApprovalAction.request_stage_id == request_stage.id)
        .all()
    )
    approved_count = sum(1 for a in actions if a.decision == models.ApprovalDecision.approved)
    rejected_count = sum(1 for a in actions if a.decision == models.ApprovalDecision.rejected)

    group_members_count = (
        db.query(models.ApproverGroupMember)
        .filter(models.ApproverGroupMember.group_id == stage_def.approver_group_id)
        .count()
    )

    now = datetime.utcnow()

    # 1. Handle rejection
    if rejected_count >= 1:
        workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
        behavior = workflow.rejection_behavior if workflow else models.RejectionBehavior.stop

        if behavior == models.RejectionBehavior.stop:
            request_stage.status = models.RequestStatus.rejected
            req.status = models.RequestStatus.rejected
            req.resolved_at = now
            return
        elif behavior == models.RejectionBehavior.restart:
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

    # 2. Approval logic
    completed = False
    if stage_def.voting_rule == models.VotingRule.any:
        if approved_count >= 1:
            completed = True
            request_stage.status = models.RequestStatus.approved

    elif stage_def.voting_rule == models.VotingRule.all:
        if approved_count >= group_members_count:
            completed = True
            request_stage.status = models.RequestStatus.approved

    elif stage_def.voting_rule == models.VotingRule.sequential:
        if approved_count >= group_members_count:
            completed = True
            request_stage.status = models.RequestStatus.approved
        else:
            # Not completed yet, notify the next person in sequence
            _fire_stage_notification(db, req, request_stage, stage_def)
            return

    if completed:
        request_stage.completed_at = now
        _advance_request(db, req)


@router.post("/", response_model=ApprovalActionOut)
def take_action(
    payload: ApprovalActionCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not current_user:
        raise HTTPException(404, "User not found")
    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == payload.request_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != models.RequestStatus.pending:
        raise HTTPException(400, f"Request is already {req.status.value}")

    active_stage = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.request_id == req.id,
            models.RequestStage.stage_order == req.current_stage,
            models.RequestStage.status == models.RequestStatus.pending
        )
        .first()
    )

    if not active_stage:
        active_stage = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req.id,
                models.RequestStage.status == models.RequestStatus.pending,
            )
            .order_by(models.RequestStage.stage_order)
            .first()
        )
        if active_stage:
            now = datetime.utcnow()
            if active_stage.started_at is None:
                stage_def_heal = db.query(models.WorkflowStage).filter(
                    models.WorkflowStage.id == active_stage.stage_id
                ).first()
                active_stage.started_at = now
                if stage_def_heal:
                    active_stage.sla_deadline = now + timedelta(hours=stage_def_heal.sla_hours)
            req.current_stage = active_stage.stage_order
            db.flush()
            db.refresh(req)

    if not active_stage:
        raise HTTPException(400, "No active stage found for this request")

    stage_def = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == active_stage.stage_id).first()
    if not stage_def:
        raise HTTPException(400, "Stage definition not found — the workflow stage may have been deleted")

    # Sequential check: is it this user's turn?
    if stage_def.voting_rule == models.VotingRule.sequential:
        acted_user_ids = [
            a.approver_id for a in
            db.query(models.ApprovalAction)
            .filter(models.ApprovalAction.request_stage_id == active_stage.id)
            .all()
        ]
        next_member = (
            db.query(models.ApproverGroupMember)
            .filter(
                models.ApproverGroupMember.group_id == stage_def.approver_group_id,
                models.ApproverGroupMember.user_id.notin_(acted_user_ids)
            )
            .order_by(models.ApproverGroupMember.sequential_order)
            .first()
        )
        if not next_member or next_member.user_id != current_user.id:
            if current_user.role != models.UserRole.admin:
                raise HTTPException(403, "It is not your turn to approve in this sequential stage")

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
    existing = (
        db.query(models.ApprovalAction)
        .filter(
            models.ApprovalAction.request_stage_id == active_stage.id,
            models.ApprovalAction.approver_id == current_user.id
        )
        .first()
    )
    if existing:
        raise HTTPException(400, "You have already acted on this stage")

    action = models.ApprovalAction(
        request_stage_id=active_stage.id,
        approver_id=current_user.id,
        decision=payload.decision,
        comment=payload.comment,
        delegated_to_id=payload.delegated_to_id if payload.decision == models.ApprovalDecision.delegated else None,
    )
    db.add(action)
    db.flush()

    verb = payload.decision.value
    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action=verb,
        detail=f"Stage {req.current_stage} {verb} by {current_user.name}. {payload.comment or ''}",
        stage_order=req.current_stage
    ))

    _check_stage_completion(db, active_stage, req)
    db.commit()
    db.refresh(action)
    return action


@router.get("/pending")
def my_pending(user_id: int = Query(...), db: Session = Depends(get_db)):
    """Return request IDs where the current user has a pending action."""
    current_user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not current_user:
        raise HTTPException(404, "User not found")

    if current_user.role == models.UserRole.admin:
        pending_requests = (
            db.query(models.WorkflowRequest.id)
            .filter(models.WorkflowRequest.status == models.RequestStatus.pending)
            .all()
        )
        return [r.id for r in pending_requests]

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
