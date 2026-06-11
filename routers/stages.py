from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from schemas import StageCreate, StageOut, ApproverGroupCreate, ApproverGroupOut
from auth_utils import require_role, get_current_user
from pydantic import BaseModel
import models

router = APIRouter()

# ─── Approver Groups ──────────────────────────────────────────────────────────

class MemberAdd(BaseModel):
    user_id: int

class ApproverGroupDetailOut(ApproverGroupOut):
    members: list = []
    class Config:
        from_attributes = True

@router.get("/approver-groups", response_model=List[ApproverGroupDetailOut])
def list_groups(db: Session = Depends(get_db), _=Depends(get_current_user)):
    groups = db.query(models.ApproverGroup).all()
    result = []
    for g in groups:
        members = [
            {"id": m.user.id, "name": m.user.name, "email": m.user.email, "role": m.user.role}
            for m in g.members if m.user
        ]
        result.append({"id": g.id, "name": g.name, "description": g.description, "members": members})
    return result

@router.post("/approver-groups", response_model=ApproverGroupDetailOut)
def create_group(
    payload: ApproverGroupCreate,
    db: Session = Depends(get_db),
    _=Depends(require_role(models.UserRole.admin))
):
    group = models.ApproverGroup(name=payload.name, description=payload.description)
    db.add(group)
    db.flush()
    for uid in payload.member_ids:
        user = db.query(models.User).filter(models.User.id == uid).first()
        if user:
            db.add(models.ApproverGroupMember(group_id=group.id, user_id=uid))
    db.commit()
    db.refresh(group)
    members = [
        {"id": m.user.id, "name": m.user.name, "email": m.user.email, "role": m.user.role}
        for m in group.members if m.user
    ]
    return {"id": group.id, "name": group.name, "description": group.description, "members": members}

@router.delete("/approver-groups/{group_id}")
def delete_group(
    group_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(models.UserRole.admin))
):
    group = db.query(models.ApproverGroup).filter(models.ApproverGroup.id == group_id).first()
    if not group:
        raise HTTPException(404, "Group not found")
    db.delete(group)
    db.commit()
    return {"detail": "Deleted"}

@router.post("/approver-groups/{group_id}/members")
def add_member(
    group_id: int,
    payload: MemberAdd,
    db: Session = Depends(get_db),
    _=Depends(require_role(models.UserRole.admin))
):
    group = db.query(models.ApproverGroup).filter(models.ApproverGroup.id == group_id).first()
    if not group:
        raise HTTPException(404, "Group not found")
    user = db.query(models.User).filter(models.User.id == payload.user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    existing = db.query(models.ApproverGroupMember).filter(
        models.ApproverGroupMember.group_id == group_id,
        models.ApproverGroupMember.user_id == payload.user_id
    ).first()
    if existing:
        raise HTTPException(400, "User already in group")
    db.add(models.ApproverGroupMember(group_id=group_id, user_id=payload.user_id))
    db.commit()
    return {"detail": "Member added"}

@router.delete("/approver-groups/{group_id}/members/{user_id}")
def remove_member(
    group_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_role(models.UserRole.admin))
):
    member = db.query(models.ApproverGroupMember).filter(
        models.ApproverGroupMember.group_id == group_id,
        models.ApproverGroupMember.user_id == user_id
    ).first()
    if not member:
        raise HTTPException(404, "Member not found")
    db.delete(member)
    db.commit()
    return {"detail": "Member removed"}

# ─── Users list (for adding to groups) ───────────────────────────────────────

@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(require_role(models.UserRole.admin))):
    users = db.query(models.User).filter(models.User.is_active == True).all()
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "department": u.department} for u in users]

# ─── Workflow Stages ──────────────────────────────────────────────────────────

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