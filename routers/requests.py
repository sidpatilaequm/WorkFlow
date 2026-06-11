from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
import os
import shutil
import uuid
from database import get_db
from schemas import RequestCreate, RequestOut
from auth_utils import get_current_user, require_role
import models

router = APIRouter()

UPLOAD_DIR = "uploads"

@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user)
):
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)
    
    # Generate unique filename to prevent collisions
    ext = os.path.splitext(file.filename)[1]
    unique_name = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Return the relative URL and original name
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
        # Start the first stage
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
    return req


@router.get("/", response_model=List[RequestOut])
def list_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if current_user.role == models.UserRole.admin:
        return db.query(models.WorkflowRequest).all()

    if current_user.role == models.UserRole.approver:
        # Get all group IDs this approver belongs to
        group_ids = [
            m.group_id for m in
            db.query(models.ApproverGroupMember)
            .filter(models.ApproverGroupMember.user_id == current_user.id)
            .all()
        ]
        # Get workflow IDs that have stages assigned to those groups
        workflow_ids = [
            ws.workflow_id for ws in
            db.query(models.WorkflowStage)
            .filter(models.WorkflowStage.approver_group_id.in_(group_ids))
            .all()
        ] if group_ids else []
        # Return all requests on those workflows + requests they submitted themselves
        return (
            db.query(models.WorkflowRequest)
            .filter(
                (models.WorkflowRequest.workflow_id.in_(workflow_ids)) |
                (models.WorkflowRequest.submitter_id == current_user.id)
            )
            .all()
        )

    # Submitter — own requests only
    return (
        db.query(models.WorkflowRequest)
        .filter(models.WorkflowRequest.submitter_id == current_user.id)
        .all()
    )


@router.get("/{req_id}", response_model=RequestOut)
def get_request(
    req_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")

    # Admins and submitters always have access
    if current_user.role == models.UserRole.admin or req.submitter_id == current_user.id:
        return req

    # Approvers who are a member of any group assigned to this request's workflow stages
    # also get read access (they need to be able to view what they're approving)
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
    if req.status != models.RequestStatus.pending:
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
