from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from schemas import WorkflowCreate, WorkflowOut, WorkflowUpdate
import models

router = APIRouter()

def _get_user_or_404(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user

@router.get("/", response_model=List[WorkflowOut])
def list_workflows(user_id: int = Query(...), db: Session = Depends(get_db)):
    _get_user_or_404(user_id, db)
    return db.query(models.Workflow).all()

@router.get("/{wf_id}", response_model=WorkflowOut)
def get_workflow(wf_id: int, user_id: int = Query(...), db: Session = Depends(get_db)):
    _get_user_or_404(user_id, db)
    wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf

@router.post("/", response_model=WorkflowOut)
def create_workflow(
    payload: WorkflowCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = _get_user_or_404(user_id, db)
    if current_user.role != models.UserRole.admin:
        raise HTTPException(403, "Admin role required")
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
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = _get_user_or_404(user_id, db)
    if current_user.role != models.UserRole.admin:
        raise HTTPException(403, "Admin role required")
    wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    update_data = payload.dict(exclude_unset=True)
    stages_data = update_data.pop("stages", None)
    for field, val in update_data.items():
        setattr(wf, field, val)
    if stages_data is not None:
        for old in list(wf.stages):
            db.delete(old)
        db.flush()
        for s in stages_data:
            db.add(models.WorkflowStage(
                workflow_id=wf.id,
                name=s["name"],
                type=s["type"],
                order=s["order"],
                approver_group_id=s["approver_group_id"],
                sla_hours=s.get("sla_hours", 48),
                voting_rule=s.get("voting_rule", models.VotingRule.any),
                condition_field=s.get("condition_field"),
                condition_op=s.get("condition_op"),
                condition_value=s.get("condition_value"),
            ))
    db.commit()
    db.refresh(wf)
    return wf

@router.delete("/{wf_id}")
def delete_workflow(wf_id: int, user_id: int = Query(...), db: Session = Depends(get_db)):
    current_user = _get_user_or_404(user_id, db)
    if current_user.role != models.UserRole.admin:
        raise HTTPException(403, "Admin role required")
    wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    db.delete(wf)
    db.commit()
    return {"detail": "Deleted"}
