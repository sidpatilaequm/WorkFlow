from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
import os
import shutil
import uuid
from database import get_db
from schemas import RequestCreate, RequestOut
from auth_utils import get_current_user, require_role, decode_approval_token
import models
from routers.approvals import _fire_stage_notification, _run_async

router = APIRouter()

UPLOAD_DIR = "uploads"


@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user)
):
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
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    wf = db.query(models.Workflow).filter(models.Workflow.id == payload.workflow_id).first()
    if not wf or not wf.is_active:
        raise HTTPException(404, "Workflow not found or inactive")

    # Auto-approve check: amount_threshold
    auto_approve = False
    if wf.amount_threshold is not None and payload.amount is not None:
        if payload.amount <= wf.amount_threshold:
            auto_approve = True

    req = models.WorkflowRequest(
        title=payload.title,
        description=payload.description,
        document_name=payload.document_name,
        document_url=payload.document_url,
        amount=payload.amount,
        department=payload.department,
        request_type=payload.request_type,
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
            detail=f"Auto-approved: amount {payload.amount} is within threshold {wf.amount_threshold}",
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

    # Fire email notification for the first stage after commit
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
    status: Optional[str] = None,
    workflow_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
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

    # Submitter — own requests only
    q = db.query(models.WorkflowRequest).filter(
        models.WorkflowRequest.submitter_id == current_user.id
    )
    if status:
        q = q.filter(models.WorkflowRequest.status == status)
    return q.all()


@router.get("/{req_id}", response_model=RequestOut)
def get_request(
    req_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
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
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
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

    # Resolve which approver is acting — use token's approver_id if present,
    # otherwise fall back to first group member (legacy tokens / any-vote stages)
    if approver_id:
        member = (
            db.query(models.ApproverGroupMember)
            .filter(
                models.ApproverGroupMember.group_id == stage_def.approver_group_id,
                models.ApproverGroupMember.user_id == approver_id,
            )
            .first()
        ) if stage_def else None
    else:
        member = (
            db.query(models.ApproverGroupMember)
            .filter(models.ApproverGroupMember.group_id == stage_def.approver_group_id)
            .first()
        ) if stage_def else None

    if not member:
        raise HTTPException(400, "No approver found for this stage")

    decision = (
        models.ApprovalDecision.approved
        if action_str == "approved"
        else models.ApprovalDecision.rejected
    )

    # Prevent duplicate
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
    ))

    # Trigger stage completion logic (reuse approvals logic inline)
    from routers.approvals import _check_stage_completion
    _check_stage_completion(db, active_stage, req)

    db.commit()
    db.refresh(req)
    return req
