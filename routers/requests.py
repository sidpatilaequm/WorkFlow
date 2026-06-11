from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timedelta
from database import get_db
from schemas import RequestCreate, RequestOut
from auth_utils import get_current_user, require_role
import models

router = APIRouter()


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
    if current_user.role != models.UserRole.admin and req.submitter_id != current_user.id:
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
