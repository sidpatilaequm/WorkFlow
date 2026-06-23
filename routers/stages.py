from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from schemas import StageCreate, StageOut, ApproverGroupCreate, ApproverGroupOut
from pydantic import BaseModel
import models

router = APIRouter()


def _require_admin(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.role != models.UserRole.admin:
        raise HTTPException(403, "Admin role required")
    return user


def _get_user_or_404(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user

# ─── Approver Groups ──────────────────────────────────────────────────────────

class MemberAdd(BaseModel):
    user_id: int
    is_optional: bool = False
    sequential_order: int = 0

class MemberOptionalUpdate(BaseModel):
    is_optional: bool
    sequential_order: int = 0

class MemberSubstitute(BaseModel):
    new_user_id: int

class ApproverGroupDetailOut(ApproverGroupOut):
    members: list = []
    class Config:
        from_attributes = True

@router.get("/approver-groups", response_model=List[ApproverGroupDetailOut])
def list_groups(user_id: int = Query(...), db: Session = Depends(get_db)):
    _get_user_or_404(user_id, db)
    groups = db.query(models.ApproverGroup).all()
    result = []
    for g in groups:
        # Sort members by sequential_order
        sorted_members = sorted(g.members, key=lambda m: m.sequential_order)
        members = [
            {
                "id": m.user.id, 
                "name": m.user.name, 
                "email": m.user.email, 
                "role": m.user.role, "is_optional": m.is_optional,
                "sequential_order": m.sequential_order
            }
            for m in sorted_members if m.user
        ]
        result.append({"id": g.id, "name": g.name, "description": g.description, "members": members})
    return result

@router.post("/approver-groups", response_model=ApproverGroupDetailOut)
def create_group(
    payload: ApproverGroupCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
    group = models.ApproverGroup(name=payload.name, description=payload.description)
    db.add(group)
    db.flush()
    # optional_member_ids and member_ids may overlap; optional wins on overlap.
    # sequential_order follows the order members were listed in member_ids,
    # with any optional-only members appended after.
    ordered_uids = list(dict.fromkeys(payload.member_ids + payload.optional_member_ids))
    optional_set = set(payload.optional_member_ids)
    for idx, uid in enumerate(ordered_uids):
        user = db.query(models.User).filter(models.User.id == uid).first()
        if user:
            db.add(models.ApproverGroupMember(
                group_id=group.id,
                user_id=uid,
                is_optional=uid in optional_set,
                sequential_order=idx,
            ))
    db.commit()
    db.refresh(group)
    sorted_members = sorted(group.members, key=lambda m: m.sequential_order)
    members = [
        {
            "id": m.user.id, 
            "name": m.user.name, 
            "email": m.user.email, 
            "role": m.user.role, "is_optional": m.is_optional,
            "sequential_order": m.sequential_order
        }
        for m in sorted_members if m.user
    ]
    return {"id": group.id, "name": group.name, "description": group.description, "members": members}

@router.delete("/approver-groups/{group_id}")
def delete_group(
    group_id: int,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
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
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
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
        existing.sequential_order = payload.sequential_order
    else:
        db.add(models.ApproverGroupMember(
            group_id=group_id, 
            user_id=payload.user_id, is_optional=payload.is_optional, 
            sequential_order=payload.sequential_order
        ))
    db.commit()
    return {"detail": "Member added/updated"}

@router.patch("/approver-groups/{group_id}/members/{member_user_id}")
def set_member_optional(
    group_id: int,
    member_user_id: int,
    payload: MemberOptionalUpdate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Toggle whether an existing member is optional (notified, but never blocks the stage)."""
    _require_admin(user_id, db)
    member = db.query(models.ApproverGroupMember).filter(
        models.ApproverGroupMember.group_id == group_id,
        models.ApproverGroupMember.user_id == member_user_id
    ).first()
    if not member:
        raise HTTPException(404, "Member not found")
    member.is_optional = payload.is_optional
    db.commit()
    return {"detail": "Updated", "is_optional": member.is_optional}

@router.delete("/approver-groups/{group_id}/members/{user_id_path}")
def remove_member(
    group_id: int,
    user_id_path: int,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
    member = db.query(models.ApproverGroupMember).filter(
        models.ApproverGroupMember.group_id == group_id,
        models.ApproverGroupMember.user_id == user_id_path,
    ).first()
    if not member:
        raise HTTPException(404, "Member not found")
    db.delete(member)
    db.commit()
    return {"detail": "Member removed"}


@router.post("/approver-groups/{group_id}/members/{user_id_path}/substitute")
def substitute_member(
    group_id: int,
    user_id_path: int,
    payload: MemberSubstitute,
    user_id: int = Query(..., description="Admin performing the substitution"),
    db: Session = Depends(get_db),
):
    """
    Swap approver `user_id_path` for `payload.new_user_id` within this group,
    in place — the membership row's sequential_order and is_optional carry
    over unchanged, only the user changes. This is the general "swap
    approver A for approver B" admin action (Workflow #3), distinct from the
    automatic OOO/delegate substitution in workflow_snapshot.py /
    approvals.py, which only kicks in for a user who's currently OOO.

    Like every other live-table edit in this router, this affects only the
    *live* group going forward; requests already in flight keep running
    against their frozen workflow_snapshot (see workflow_snapshot.py) and
    are unaffected.
    """
    _require_admin(user_id, db)

    group = db.query(models.ApproverGroup).filter(models.ApproverGroup.id == group_id).first()
    if not group:
        raise HTTPException(404, "Group not found")

    member = db.query(models.ApproverGroupMember).filter(
        models.ApproverGroupMember.group_id == group_id,
        models.ApproverGroupMember.user_id == user_id_path,
    ).first()
    if not member:
        raise HTTPException(404, "Member not found")

    new_user = db.query(models.User).filter(
        models.User.id == payload.new_user_id, models.User.is_active == True
    ).first()
    if not new_user:
        raise HTTPException(404, "Substitute user not found")

    if payload.new_user_id == user_id_path:
        raise HTTPException(400, "new_user_id must differ from the member being substituted")

    already_in_group = db.query(models.ApproverGroupMember).filter(
        models.ApproverGroupMember.group_id == group_id,
        models.ApproverGroupMember.user_id == payload.new_user_id,
    ).first()
    if already_in_group:
        raise HTTPException(400, "Substitute user is already a member of this group")

    member.user_id = payload.new_user_id
    db.commit()
    return {
        "detail": "Member substituted",
        "group_id": group_id,
        "replaced_user_id": user_id_path,
        "new_user_id": payload.new_user_id,
    }

# ─── Users list (for adding to groups) ───────────────────────────────────────

@router.get("/users")
def list_users(user_id: int = Query(...), db: Session = Depends(get_db)):
    _require_admin(user_id, db)
    users = db.query(models.User).filter(models.User.is_active == True).all()
    return [
        {
            "id": u.id, "name": u.name, "email": u.email, "role": u.role, "company_id": u.company_id,
            "ooo_until": u.ooo_until, "delegate_id": u.delegate_id,
        }
        for u in users
    ]

# ─── Workflow Stages ──────────────────────────────────────────────────────────

@router.post("/{wf_id}/stages", response_model=StageOut)
def add_stage(
    wf_id: int,
    payload: StageCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
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
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    _require_admin(user_id, db)
    stage = db.query(models.WorkflowStage).filter(models.WorkflowStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Stage not found")
    db.delete(stage)
    db.commit()
    return {"detail": "Deleted"}
