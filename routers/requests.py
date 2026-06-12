from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
import os
import shutil
import uuid
from database import get_db
from schemas import RequestCreate, RequestOut
from auth_utils import decode_approval_token
import models
from routers.approvals import _fire_stage_notification, _run_async

router = APIRouter()

UPLOAD_DIR = "uploads"


def _get_user_or_404(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _get_user_or_404(user_id, db)
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)

    ext = os.path.splitext(file.filename)[1]
    unique_name = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {
        "document_name": file.filename,
        "document_url": f"/uploads/{unique_name}"
    }


@router.post("/", response_model=RequestOut, status_code=201)
def submit_request(
    payload: RequestCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = _get_user_or_404(user_id, db)
    wf = db.query(models.Workflow).filter(models.Workflow.id == payload.workflow_id).first()
    if not wf or not wf.is_active:
        raise HTTPException(404, "Workflow not found or inactive")

    auto_approve = False
    # 1. Simple amount threshold check
    if wf.amount_threshold is not None and payload.amount is not None:
        if payload.amount <= wf.amount_threshold:
            auto_approve = True

    # 2. Complex auto-approve conditions (JSON)
    if not auto_approve and wf.auto_approve_conditions:
        try:
            # Expected format: [{"field": "amount", "operator": "lt", "value": 1000}, ...]
            conditions = wf.auto_approve_conditions
            if isinstance(conditions, list):
                all_met = True
                for cond in conditions:
                    f = cond.get("field")
                    op = cond.get("operator")
                    val = cond.get("value")
                    actual = getattr(payload, f, None)

                    if op == "lt": all_met = all_met and (actual < val)
                    elif op == "lte": all_met = all_met and (actual <= val)
                    elif op == "gt": all_met = all_met and (actual > val)
                    elif op == "gte": all_met = all_met and (actual >= val)
                    elif op == "eq": all_met = all_met and (actual == val)
                    else: all_met = False
                
                if all_met:
                    auto_approve = True
        except Exception as e:
            print(f"Error evaluating auto-approve conditions: {e}")

    req = models.WorkflowRequest(
        title=payload.title,
        description=payload.description,
        document_name=payload.document_name,
        document_url=payload.document_url,
        document_type=payload.document_type,
        folder_path=payload.folder_path,
        amount=payload.amount,
        department=payload.department,
        request_type=payload.request_type,
        request_metadata=payload.request_metadata,
        workflow_id=wf.id,
        submitter_id=current_user.id,
        current_stage=0,
    )
    db.add(req)
    db.flush()

    if auto_approve:
        req.status = models.RequestStatus.approved
        req.resolved_at = datetime.utcnow()
        db.add(models.ActivityLog(
            request_id=req.id,
            user_id=None,
            action="auto_approved",
            detail=f"Auto-approved based on workflow conditions",
        ))
        db.commit()
        db.refresh(req)
        return req

    now = datetime.utcnow()
    sorted_stages = sorted(wf.stages, key=lambda s: s.order)
    for idx, stage_def in enumerate(sorted_stages):
        rs = models.RequestStage(
            request_id=req.id,
            stage_id=stage_def.id,
            stage_order=stage_def.order,
        )
        db.add(rs)
        db.flush()
        if idx == 0:
            rs.started_at = now
            rs.status = models.RequestStatus.pending
            rs.sla_deadline = now + timedelta(hours=stage_def.sla_hours)
            req.current_stage = stage_def.order

    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action="submitted",
        detail=f"Request submitted by {current_user.name}",
    ))

    db.commit()
    db.refresh(req)

    if sorted_stages:
        first_stage_def = sorted_stages[0]
        first_request_stage = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req.id,
                models.RequestStage.stage_order == first_stage_def.order,
            )
            .first()
        )
        if first_request_stage:
            _fire_stage_notification(db, req, first_request_stage, first_stage_def)

    return req


@router.get("/", response_model=List[RequestOut])
def list_requests(
    user_id: int = Query(...),
    status: Optional[str] = None,
    workflow_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    current_user = _get_user_or_404(user_id, db)
    if current_user.role == models.UserRole.admin:
        q = db.query(models.WorkflowRequest)
        if status:
            q = q.filter(models.WorkflowRequest.status == status)
        if workflow_id:
            q = q.filter(models.WorkflowRequest.workflow_id == workflow_id)
        return q.all()

    if current_user.role == models.UserRole.approver:
        group_ids = [
            m.group_id for m in
            db.query(models.ApproverGroupMember)
            .filter(models.ApproverGroupMember.user_id == current_user.id)
            .all()
        ]
        workflow_ids = [
            ws.workflow_id for ws in
            db.query(models.WorkflowStage)
            .filter(models.WorkflowStage.approver_group_id.in_(group_ids))
            .all()
        ] if group_ids else []
        q = db.query(models.WorkflowRequest).filter(
            (models.WorkflowRequest.workflow_id.in_(workflow_ids)) |
            (models.WorkflowRequest.submitter_id == current_user.id)
        )
        if status:
            q = q.filter(models.WorkflowRequest.status == status)
        return q.all()

    q = db.query(models.WorkflowRequest).filter(
        models.WorkflowRequest.submitter_id == current_user.id
    )
    if status:
        q = q.filter(models.WorkflowRequest.status == status)
    return q.all()


@router.get("/{req_id}", response_model=RequestOut)
def get_request(
    req_id: int,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = _get_user_or_404(user_id, db)
    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")

    if current_user.role == models.UserRole.admin or req.submitter_id == current_user.id:
        return req

    approver_group_ids = [
        ws.approver_group_id
        for ws in db.query(models.WorkflowStage)
        .filter(models.WorkflowStage.workflow_id == req.workflow_id)
        .all()
    ]
    is_group_member = (
        db.query(models.ApproverGroupMember)
        .filter(
            models.ApproverGroupMember.user_id == current_user.id,
            models.ApproverGroupMember.group_id.in_(approver_group_ids),
        )
        .first()
    )
    if not is_group_member:
        raise HTTPException(403, "Access denied")
    return req


@router.patch("/{req_id}/cancel")
def cancel_request(
    req_id: int,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = _get_user_or_404(user_id, db)
    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.submitter_id != current_user.id and current_user.role != models.UserRole.admin:
        raise HTTPException(403, "Access denied")
    if req.status not in (models.RequestStatus.pending,):
        raise HTTPException(400, f"Cannot cancel a request with status '{req.status.value}'")
    req.status = models.RequestStatus.cancelled
    req.resolved_at = datetime.utcnow()
    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action="cancelled",
        detail=f"Request cancelled by {current_user.name}",
    ))
    db.commit()
    return {"detail": "Request cancelled"}


@router.get("/action/{token}", response_model=RequestOut)
def one_click_action(
    token: str,
    db: Session = Depends(get_db),
):
    """
    Email link handler — approve or reject via a signed token, no login required.
    Token carries: request_id, stage_order, action ('approved'|'rejected').
    """
    payload = decode_approval_token(token)
    request_id = payload["request_id"]
    stage_order = payload["stage_order"]
    action_str = payload["action"]
    approver_id = payload.get("approver_id")

    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == request_id).first()
    if not req:
        raise HTTPException(404, "Request not found")

    if req.status != models.RequestStatus.pending:
        raise HTTPException(400, f"Request is already {req.status.value}")

    active_stage = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.request_id == request_id,
            models.RequestStage.stage_order == stage_order,
            models.RequestStage.status == models.RequestStatus.pending,
        )
        .first()
    )
    if not active_stage:
        raise HTTPException(400, "Stage no longer pending or not found")

    stage_def = db.query(models.WorkflowStage).filter(
        models.WorkflowStage.id == active_stage.stage_id
    ).first()

    if not approver_id:
        raise HTTPException(400, "Approver ID missing from token")

    # Sequential check
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
        if not next_member or next_member.user_id != approver_id:
            raise HTTPException(403, "It is not your turn to approve in this sequential stage")

    member = (
        db.query(models.ApproverGroupMember)
        .filter(
            models.ApproverGroupMember.group_id == stage_def.approver_group_id,
            models.ApproverGroupMember.user_id == approver_id,
        )
        .first()
    )

    if not member:
        raise HTTPException(400, "You are not an approver for this stage")

    decision = (
        models.ApprovalDecision.approved
        if action_str == "approved"
        else models.ApprovalDecision.rejected
    )

    existing = (
        db.query(models.ApprovalAction)
        .filter(
            models.ApprovalAction.request_stage_id == active_stage.id,
            models.ApprovalAction.approver_id == member.user_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(400, "Action already recorded for this stage")

    action = models.ApprovalAction(
        request_stage_id=active_stage.id,
        approver_id=member.user_id,
        decision=decision,
        comment="Via email link",
    )
    db.add(action)
    db.flush()

    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=member.user_id,
        action=action_str,
        detail=f"Stage {stage_order} {action_str} via email link",
        stage_order=stage_order
    ))

    from routers.approvals import _check_stage_completion
    _check_stage_completion(db, active_stage, req)

    db.commit()
    db.refresh(req)
    return req


@router.post("/action/{req_id}", response_model=RequestOut)
def take_action_by_user(
    req_id: int,
    action: str = Query(..., description="'approved' or 'rejected'"),
    user_id: int = Query(...),
    comment: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    In-app action handler — approve or reject a request stage using user_id,
    consistent with all other endpoints in this router.
    """
    if action not in ("approved", "rejected"):
        raise HTTPException(400, "action must be 'approved' or 'rejected'")

    current_user = _get_user_or_404(user_id, db)

    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")

    if req.status != models.RequestStatus.pending:
        raise HTTPException(400, f"Request is already {req.status.value}")

    # Find the currently active stage for this request
    active_stage = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.request_id == req_id,
            models.RequestStage.stage_order == req.current_stage,
            models.RequestStage.status == models.RequestStatus.pending,
        )
        .first()
    )
    if not active_stage:
        # Self-heal: fall back to the lowest-order pending stage
        active_stage = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req_id,
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

    stage_def = db.query(models.WorkflowStage).filter(
        models.WorkflowStage.id == active_stage.stage_id
    ).first()

    # Sequential check
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

    # Verify the user belongs to the approver group for this stage
    if current_user.role not in (models.UserRole.admin,):
        membership = (
            db.query(models.ApproverGroupMember)
            .filter(
                models.ApproverGroupMember.group_id == stage_def.approver_group_id,
                models.ApproverGroupMember.user_id == current_user.id,
            )
            .first()
        ) if stage_def else None
        if not membership:
            raise HTTPException(403, "You are not in the approver group for this stage")

    # Prevent duplicate action
    existing = (
        db.query(models.ApprovalAction)
        .filter(
            models.ApprovalAction.request_stage_id == active_stage.id,
            models.ApprovalAction.approver_id == current_user.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(400, "You have already acted on this stage")

    decision = (
        models.ApprovalDecision.approved
        if action == "approved"
        else models.ApprovalDecision.rejected
    )

    approval_action = models.ApprovalAction(
        request_stage_id=active_stage.id,
        approver_id=current_user.id,
        decision=decision,
        comment=comment or "Via in-app action",
    )
    db.add(approval_action)
    db.flush()

    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action=action,
        detail=f"Stage {req.current_stage} {action} by {current_user.name}. {comment or ''}",
        stage_order=req.current_stage
    ))

    from routers.approvals import _check_stage_completion
    _check_stage_completion(db, active_stage, req)

    db.commit()
    db.refresh(req)
    return req
