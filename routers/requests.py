from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timedelta
import os
import shutil
import uuid
from dotenv import load_dotenv
from database import get_db
from schemas import RequestCreate, RequestOut, ManualMessageCreate
from auth_utils import decode_approval_token, get_current_user
import models
from routers.approvals import _fire_stage_notification, _run_async
from services.notification import notification_service
from workflow_snapshot import get_stage_config, member_ids as snapshot_member_ids, next_sequential_member
from template_utils import resolve_template_variables, render_template

load_dotenv()
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

router = APIRouter()

UPLOAD_DIR = "uploads"


def _get_user_or_404(user_id: int, db: Session) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not user:
        raise HTTPException(404, "User not found")
    return user


def _build_workflow_snapshot(wf: models.Workflow) -> dict:
    """
    Freeze the workflow's current stage/approver-group config into a plain
    JSON-serializable dict, stored on WorkflowRequest.workflow_snapshot at
    submission time. Later edits to the live Workflow/WorkflowStage/
    ApproverGroup rows (renaming a stage, changing voting rule, swapping
    group members, etc) never retroactively change what an already-submitted
    request shows OR runs as "the approval chain it was submitted under" —
    see workflow_snapshot.py:get_stage_config, which the approval-flow
    functions read from instead of the live tables.
    """
    stages_snapshot = []
    for s in sorted(wf.stages, key=lambda x: x.order):
        group = s.approver_group
        members = []
        if group:
            for m in sorted(group.members, key=lambda mm: mm.sequential_order):
                if m.user:
                    members.append({
                        "user_id": m.user.id,
                        "name": m.user.name,
                        "email": m.user.email,
                        "is_optional": m.is_optional,
                        "sequential_order": m.sequential_order,
                    })
        stages_snapshot.append({
            "stage_id": s.id,
            "order": s.order,
            "name": s.name,
            "type": s.type.value if s.type else None,
            "sla_hours": s.sla_hours,
            "voting_rule": s.voting_rule.value if s.voting_rule else None,
            "approve_label": s.approve_label,
            "reject_label": s.reject_label,
            "is_optional": s.is_optional,
            "parallel_group": s.parallel_group,
            "instructions": s.instructions,
            "approver_group_id": s.approver_group_id,
            "approver_group_name": group.name if group else None,
            "members": members,
        })
    return {
        "workflow_id": wf.id,
        "workflow_name": wf.name,
        "captured_at": datetime.utcnow().isoformat(),
        "stages": stages_snapshot,
    }


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
        workflow_snapshot=_build_workflow_snapshot(wf),
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

    # Start the first stage (or, if it belongs to a parallel group, all stages
    # in that group) and fire notifications for each started stage.
    if sorted_stages:
        first_stage_def = sorted_stages[0]
        first_pg = first_stage_def.parallel_group

        # Collect all stages that should start simultaneously with stage 1.
        # If stage 1 has a parallel_group, all stages sharing that group start now;
        # otherwise only stage 1 starts.
        if first_pg is not None:
            stages_to_start = [s for s in sorted_stages if s.parallel_group == first_pg]
        else:
            stages_to_start = [first_stage_def]

        for stage_def in stages_to_start:
            rs = (
                db.query(models.RequestStage)
                .filter(
                    models.RequestStage.request_id == req.id,
                    models.RequestStage.stage_order == stage_def.order,
                )
                .first()
            )
            if rs and rs.started_at is None:
                rs.started_at = now
                rs.status = models.RequestStatus.pending
                rs.sla_deadline = now + timedelta(hours=stage_def.sla_hours)

        db.flush()
        # current_stage stays as the lowest order in the parallel group
        req.current_stage = stages_to_start[0].order
        db.commit()
        db.refresh(req)

        for stage_def in stages_to_start:
            rs = (
                db.query(models.RequestStage)
                .filter(
                    models.RequestStage.request_id == req.id,
                    models.RequestStage.stage_order == stage_def.order,
                )
                .first()
            )
            if rs:
                stage_cfg = get_stage_config(db, req, stage_def.order, stage_def.id)
                _fire_stage_notification(db, req, rs, stage_cfg)

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
        now = datetime.utcnow()
        own_group_ids = [
            m.group_id for m in
            db.query(models.ApproverGroupMember)
            .filter(models.ApproverGroupMember.user_id == current_user.id)
            .all()
        ]
        # Also include groups where current_user is standing in as the
        # delegate for a currently out-of-office member, even though they
        # aren't a member of that group themselves — keeps this in sync with
        # the OOO stand-in logic in approvals.py (take_action / my_pending).
        delegate_group_ids = [
            m.group_id for m in
            db.query(models.ApproverGroupMember)
            .join(models.User, models.ApproverGroupMember.user_id == models.User.id)
            .filter(
                models.User.delegate_id == current_user.id,
                models.User.ooo_until.isnot(None),
                models.User.ooo_until > now,
            )
            .all()
        ]
        group_ids = list(set(own_group_ids + delegate_group_ids))
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
        if workflow_id:
            q = q.filter(models.WorkflowRequest.workflow_id == workflow_id)
        return q.all()

    q = db.query(models.WorkflowRequest).filter(
        models.WorkflowRequest.submitter_id == current_user.id
    )
    if status:
        q = q.filter(models.WorkflowRequest.status == status)
    if workflow_id:
        q = q.filter(models.WorkflowRequest.workflow_id == workflow_id)
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
        # Also allow access if current_user is standing in as the delegate
        # for a currently out-of-office member of one of these groups.
        now = datetime.utcnow()
        is_delegate_for_member = (
            db.query(models.ApproverGroupMember)
            .join(models.User, models.ApproverGroupMember.user_id == models.User.id)
            .filter(
                models.ApproverGroupMember.group_id.in_(approver_group_ids),
                models.User.delegate_id == current_user.id,
                models.User.ooo_until.isnot(None),
                models.User.ooo_until > now,
            )
            .first()
        )
        if not is_delegate_for_member:
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


def _process_action_token(token: str, db: Session):
    """
    Core logic shared by the JSON endpoint and the browser-redirect endpoint.
    Token carries: request_id, stage_order, action ('approved'|'rejected').
    Returns (req, action_str). Raises HTTPException on any failure.
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

    # Frozen-at-submission stage config (falls back to live tables for
    # pre-snapshot requests) — see workflow_snapshot.py.
    stage_cfg = get_stage_config(db, req, active_stage.stage_order, active_stage.stage_id)
    if not stage_cfg:
        raise HTTPException(400, "Stage definition not found")

    if not approver_id:
        raise HTTPException(400, "Approver ID missing from token")

    # Sequential check
    if stage_cfg.get("voting_rule") == models.VotingRule.sequential.value:
        acted_user_ids = {
            a.approver_id for a in
            db.query(models.ApprovalAction)
            .filter(models.ApprovalAction.request_stage_id == active_stage.id)
            .all()
        }
        next_member = next_sequential_member(stage_cfg, acted_user_ids)
        if not next_member or next_member["user_id"] != approver_id:
            raise HTTPException(403, "It is not your turn to approve in this sequential stage")

    member = next(
        (m for m in stage_cfg.get("members", []) if m["user_id"] == approver_id),
        None,
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
            models.ApprovalAction.approver_id == member["user_id"],
        )
        .first()
    )
    if existing:
        raise HTTPException(400, "Action already recorded for this stage")

    action = models.ApprovalAction(
        request_stage_id=active_stage.id,
        approver_id=member["user_id"],
        decision=decision,
        comment="Via email link",
    )
    db.add(action)
    db.flush()

    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=member["user_id"],
        action=action_str,
        detail=f"Stage {stage_order} {action_str} via email link",
        stage_order=stage_order
    ))

    from routers.approvals import _check_stage_completion
    _check_stage_completion(db, active_stage, req)

    db.commit()
    db.refresh(req)
    return req, action_str


@router.post("/action/{req_id}", response_model=RequestOut)
def take_action_by_user(
    req_id: int,
    action: str = Query(..., description="'approved' or 'rejected'"),
    user_id: int = Query(...),
    comment: Optional[str] = Query(None),
    document_name: Optional[str] = Query(None, description="Optional document attached to this decision"),
    document_url: Optional[str] = Query(None, description="URL returned by POST /requests/upload, if a document was attached"),
    db: Session = Depends(get_db),
):
    """
    In-app action handler — approve or reject a request stage using user_id,
    consistent with all other endpoints in this router. Optionally attach a
    document (upload it first via POST /requests/upload, then pass the
    returned document_name/document_url here).
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
                stage_cfg_heal = get_stage_config(db, req, active_stage.stage_order, active_stage.stage_id)
                active_stage.started_at = now
                if stage_cfg_heal:
                    active_stage.sla_deadline = now + timedelta(hours=stage_cfg_heal.get("sla_hours") or 48)
            req.current_stage = active_stage.stage_order
            db.flush()
            db.refresh(req)

    if not active_stage:
        raise HTTPException(400, "No active stage found for this request")

    stage_cfg = get_stage_config(db, req, active_stage.stage_order, active_stage.stage_id)
    if not stage_cfg:
        raise HTTPException(400, "Stage definition not found — the workflow stage may have been deleted")

    members_by_id = snapshot_member_ids(stage_cfg)

    # Sequential check
    if stage_cfg.get("voting_rule") == models.VotingRule.sequential.value:
        acted_user_ids = {
            a.approver_id for a in
            db.query(models.ApprovalAction)
            .filter(models.ApprovalAction.request_stage_id == active_stage.id)
            .all()
        }
        next_member = next_sequential_member(stage_cfg, acted_user_ids)
        if not next_member or next_member["user_id"] != current_user.id:
            if current_user.role != models.UserRole.admin:
                raise HTTPException(403, "It is not your turn to approve in this sequential stage")

    # Verify the user belongs to the approver group for this stage (per the
    # frozen stage_cfg)
    if current_user.role not in (models.UserRole.admin,):
        if current_user.id not in members_by_id:
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
        document_name=document_name,
        document_url=document_url,
    )
    db.add(approval_action)
    db.flush()

    detail = f"Stage {req.current_stage} {action} by {current_user.name}"
    if document_url:
        detail += f" with document attached ({document_name or document_url})"
    detail += f". {comment or ''}"
    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action=action,
        detail=detail,
        stage_order=req.current_stage
    ))

    from routers.approvals import _check_stage_completion
    _check_stage_completion(db, active_stage, req)

    db.commit()
    db.refresh(req)
    return req


@router.get("/action/{token}", response_model=RequestOut)
def one_click_action(
    token: str,
    db: Session = Depends(get_db),
):
    """
    Email link handler — approve or reject via a signed token, no login required.
    Returns the updated request as JSON. For a plain browser redirect instead,
    see GET /action/{token}/redirect.
    """
    req, _ = _process_action_token(token, db)
    return req


@router.get("/action/{token}/redirect")
def one_click_action_redirect(
    token: str,
    db: Session = Depends(get_db),
):
    """
    Browser-friendly variant of /action/{token}: performs the same
    approve/reject action, then issues an HTTP redirect to the workflow's
    configured success_redirect_url / failure_redirect_url. Falls back to
    the frontend's request page when no redirect URL is configured on the
    workflow.
    """
    try:
        req, action_str = _process_action_token(token, db)
    except HTTPException as exc:
        return RedirectResponse(url=f"{FRONTEND_URL}/action-error?detail={exc.detail}")

    workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
    if action_str == "rejected":
        target = (workflow.failure_redirect_url if workflow else None) or f"{FRONTEND_URL}/requests/{req.id}"
    else:
        target = (workflow.success_redirect_url if workflow else None) or f"{FRONTEND_URL}/requests/{req.id}"
    return RedirectResponse(url=target)


def _resolve_message_recipients(db: Session, req: models.WorkflowRequest, payload: ManualMessageCreate) -> List[str]:
    recipients: List[str] = []
    if payload.to == "submitter":
        submitter = db.query(models.User).filter(models.User.id == req.submitter_id).first()
        if submitter and submitter.email:
            recipients = [submitter.email]
    elif payload.to == "current_approvers":
        stage_def = (
            db.query(models.WorkflowStage)
            .filter(
                models.WorkflowStage.workflow_id == req.workflow_id,
                models.WorkflowStage.order == req.current_stage,
            )
            .first()
        )
        if stage_def and stage_def.approver_group_id:
            members = (
                db.query(models.ApproverGroupMember)
                .filter(models.ApproverGroupMember.group_id == stage_def.approver_group_id)
                .all()
            )
            recipients = [m.user.email for m in members if m.user and m.user.email]
    elif payload.to == "custom":
        recipients = payload.custom_emails or []
    else:
        raise HTTPException(400, "to must be one of: submitter, current_approvers, custom")
    return recipients


@router.post("/{req_id}/send-message")
def send_message(
    req_id: int,
    payload: ManualMessageCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """
    Send a one-off message tied to a request, independent of stage/SLA state.

    The message body/subject may reference {{field}} placeholders — base
    request fields (amount, title, department, ...), keys from
    request_metadata, and any derived formulas configured on the workflow
    (Workflow.message_variables) — rendered via template_utils.

    If reminder_interval_hours is supplied, the message is also persisted as
    a ScheduledMessage and re-sent on that cadence by
    services/escalation.py:send_message_reminders for as long as the
    request stays "pending" (i.e. the status it's meant to prompt hasn't
    been achieved), up to max_reminders times.
    """
    current_user = _get_user_or_404(user_id, db)
    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Request not found")

    # Same access rule as GET /{req_id}: submitter, admin, or a member of an
    # approver group attached to this request's workflow.
    if current_user.role != models.UserRole.admin and req.submitter_id != current_user.id:
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

    recipients = _resolve_message_recipients(db, req, payload)
    if not recipients:
        raise HTTPException(400, "No recipients resolved for this message")

    workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
    variables = resolve_template_variables(req, workflow)
    rendered_message = render_template(payload.message, variables)
    rendered_subject = render_template(payload.subject, variables)

    _run_async(
        notification_service.notify_custom_message(
            to=recipients,
            request=req,
            message=rendered_message,
            sender_name=current_user.name,
            subject=rendered_subject,
        )
    )

    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action="manual_message",
        detail=f"{current_user.name} sent a message to {payload.to} ({len(recipients)} recipient(s))",
    ))

    if payload.reminder_interval_hours:
        db.add(models.ScheduledMessage(
            request_id=req.id,
            sender_id=current_user.id,
            to=payload.to,
            custom_emails=payload.custom_emails,
            subject=payload.subject,
            message=payload.message,
            reminder_interval_hours=payload.reminder_interval_hours,
            max_reminders=payload.max_reminders,
            last_sent_at=datetime.utcnow(),
        ))

    db.commit()

    return {"detail": "Message sent", "recipients": recipients}
