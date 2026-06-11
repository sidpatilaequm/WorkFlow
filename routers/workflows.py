from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from schemas import WorkflowCreate, WorkflowOut, WorkflowUpdate
from auth_utils import get_current_user, require_role
import models

router = APIRouter()

@router.get("/", response_model=List[WorkflowOut])
def list_workflows(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.Workflow).all()

@router.get("/{wf_id}", response_model=WorkflowOut)
def get_workflow(wf_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf

@router.post("/", response_model=WorkflowOut)
def create_workflow(
    payload: WorkflowCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_role(models.UserRole.admin))
):
    wf = models.Workflow(
        name=payload.name,
        description=payload.description,
        type=payload.type,
        folder_trigger=payload.folder_trigger,
        escalation_hours=payload.escalation_hours,
        rejection_behavior=payload.rejection_behavior,
        notification_channel=payload.notification_channel,
        auto_approve_hours=payload.auto_approve_hours,
        amount_threshold=payload.amount_threshold,
        created_by_id=current_user.id,
    )
    db.add(wf)
    db.flush()

    for s in payload.stages:
        stage = models.WorkflowStage(
            workflow_id=wf.id,
            name=s.name,
            type=s.type,
            order=s.order,
            approver_group_id=s.approver_group_id,
            sla_hours=s.sla_hours,
            voting_rule=s.voting_rule,
            condition_field=s.condition_field,
            condition_op=s.condition_op,
            condition_value=s.condition_value,
        )
        db.add(stage)

    db.commit()
    db.refresh(wf)
    return wf

@router.patch("/{wf_id}", response_model=WorkflowOut)
def update_workflow(
    wf_id: int,
    payload: WorkflowUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_role(models.UserRole.admin))
):
    wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    for field, val in payload.dict(exclude_unset=True).items():
        setattr(wf, field, val)
    db.commit()
    db.refresh(wf)
    return wf

@router.delete("/{wf_id}")
def delete_workflow(wf_id: int, db: Session = Depends(get_db), _=Depends(require_role(models.UserRole.admin))):
    wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    db.delete(wf)
    db.commit()
    return {"detail": "Deleted"}