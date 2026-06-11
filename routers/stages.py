from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from schemas import StageCreate, StageOut
from auth_utils import require_role
import models

router = APIRouter()

@router.post("/{wf_id}/stages", response_model=StageOut)
def add_stage(
    wf_id: int,
    payload: StageCreate,
    db: Session = Depends(get_db),
    _=Depends(require_role(models.UserRole.admin))
):
    wf = db.query(models.Workflow).filter(models.Workflow.id == wf_id).first()
    if not wf:
        raise HTTPException(404, "Workflow not found")
    stage = models.WorkflowStage(workflow_id=wf_id, **payload.dict())
    db.add(stage)
    db.commit()
    db.refresh(stage)
    return stage

@router.delete("/{stage_id}")
def delete_stage(
    stage_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(models.UserRole.admin))
):
    stage = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")
    db.delete(stage)
    db.commit()
    return {"detail": "Deleted"}