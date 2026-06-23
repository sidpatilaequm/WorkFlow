from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from database import get_db
from schemas import ApprovalActionCreate, ApprovalActionOut
from auth_utils import create_approval_token, resolve_approver
from services.notification import notification_service
from workflow_snapshot import get_stage_config, member_ids as snapshot_member_ids, next_sequential_member
from template_utils import resolve_template_variables, render_template
import asyncio
import threading
import models


def _run_async(coro):
    """Fire a coroutine from a sync context without conflicting with uvicorn's loop."""
    def _target():
        asyncio.run(coro)
    t = threading.Thread(target=_target, daemon=True)
    t.start()

router = APIRouter()


def _advance_request(db: Session, req: models.WorkflowRequest):
    """Move request to next stage (or parallel group) or mark final status.

    Parallel group semantics: stages sharing the same non-null parallel_group
    all start simultaneously and ALL must reach an approved/skipped state
    before the workflow advances past the group. This function is called
    after each individual stage completes; if other stages in the same
    parallel group are still pending it does nothing and returns. Once
    the full group is done it advances to the next serial stage or group.
    """
    now = datetime.utcnow()

    # Determine the parallel_group of the stage that just completed so we can
    # check whether the whole group is done before advancing.
    current_rs = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.request_id == req.id,
            models.RequestStage.stage_order == req.current_stage,
        )
        .first()
    )
    current_pg = None
    if current_rs:
        cfg = get_stage_config(db, req, current_rs.stage_order, current_rs.stage_id)
        if cfg:
            current_pg = cfg.get("parallel_group")

    # If the just-completed stage belongs to a parallel group, check that all
    # sibling stages in the group are also done before advancing.
    if current_pg is not None:
        # Find all RequestStages whose snapshot parallel_group == current_pg
        all_stages = (
            db.query(models.RequestStage)
            .filter(models.RequestStage.request_id == req.id)
            .all()
        )
        group_stages = []
        for rs in all_stages:
            cfg = get_stage_config(db, req, rs.stage_order, rs.stage_id)
            if cfg and cfg.get("parallel_group") == current_pg:
                group_stages.append(rs)

        # All group stages must be in a terminal state (approved, or skipped)
        pending_in_group = [
            rs for rs in group_stages
            if rs.status == models.RequestStatus.pending
        ]
        if pending_in_group:
            # Other parallel stages still running — not time to advance yet.
            return

        # Highest order in the group is the "virtual" stage we advance past.
        max_group_order = max(rs.stage_order for rs in group_stages)
        advance_from_order = max_group_order
    else:
        advance_from_order = req.current_stage

    while True:
        next_stage = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req.id,
                models.RequestStage.stage_order > advance_from_order,
                models.RequestStage.status == models.RequestStatus.pending
            )
            .order_by(models.RequestStage.stage_order)
            .first()
        )

        if not next_stage:
            req.status = models.RequestStatus.approved
            req.resolved_at = now
            _fire_completion_notification(db, req)
            return

        next_stage_cfg = get_stage_config(db, req, next_stage.stage_order, next_stage.stage_id)

        # Auto-skip optional stages with no members
        if next_stage_cfg and next_stage_cfg.get("is_optional"):
            member_count = len(next_stage_cfg.get("members", []))
            if member_count == 0:
                next_stage.status = models.RequestStatus.approved
                next_stage.completed_at = now
                advance_from_order = next_stage.stage_order
                req.current_stage = next_stage.stage_order
                db.add(models.ActivityLog(
                    request_id=req.id,
                    action="skipped",
                    detail=f"Optional stage '{next_stage_cfg.get('name')}' skipped (no members)",
                    stage_order=next_stage.stage_order
                ))
                continue

        # Determine if next_stage starts a parallel group
        next_pg = next_stage_cfg.get("parallel_group") if next_stage_cfg else None

        if next_pg is not None:
            # Start ALL stages in the next parallel group simultaneously
            all_remaining = (
                db.query(models.RequestStage)
                .filter(
                    models.RequestStage.request_id == req.id,
                    models.RequestStage.stage_order > advance_from_order,
                    models.RequestStage.status == models.RequestStatus.pending,
                )
                .all()
            )
            group_to_start = []
            for rs in all_remaining:
                cfg = get_stage_config(db, req, rs.stage_order, rs.stage_id)
                if cfg and cfg.get("parallel_group") == next_pg:
                    group_to_start.append((rs, cfg))

            req.current_stage = group_to_start[0][0].stage_order if group_to_start else next_stage.stage_order
            for rs, cfg in group_to_start:
                rs.started_at = now
                rs.sla_deadline = now + timedelta(hours=cfg.get("sla_hours") or 48)
                _fire_stage_notification(db, rs.request, rs, cfg)
            return
        else:
            # Serial: start only this one stage
            req.current_stage = next_stage.stage_order
            next_stage.started_at = now
            if next_stage_cfg:
                next_stage.sla_deadline = now + timedelta(hours=next_stage_cfg.get("sla_hours") or 48)
            _fire_stage_notification(db, req, next_stage, next_stage_cfg)
            return


def _fire_stage_notification(db, req, request_stage, stage_cfg):
    """Send email/Slack to all approvers for a newly started stage.

    `stage_cfg` is the frozen stage config dict from workflow_snapshot.get_stage_config
    (NOT a live models.WorkflowStage row) — so the member list reflects who
    was in the approver group at submission time, even if group membership
    has since changed.

    Members who are out-of-office (User.ooo_until in the future) and have a
    delegate set are not emailed directly — their delegate is emailed instead.
    OOO/delegate status is read live (it's inherently real-time), but only
    for user_ids that are members per the frozen stage_cfg. The approval
    token still carries the *original* member's user_id, so whichever of
    them clicks, the action is recorded against the original member's slot
    and voting counts stay correct.
    """
    if not stage_cfg:
        return
    workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
    if not workflow:
        return
    channel = workflow.notification_channel

    all_members = stage_cfg.get("members", [])

    # Handle sequential voting: only notify the first/next member
    if stage_cfg.get("voting_rule") == models.VotingRule.sequential.value:
        acted_user_ids = {
            a.approver_id for a in
            db.query(models.ApprovalAction)
            .filter(models.ApprovalAction.request_stage_id == request_stage.id)
            .all()
        }
        next_member = next_sequential_member(stage_cfg, acted_user_ids)
        if not next_member:
            return
        members = [next_member]
    else:
        # All members for Any/All
        members = all_members

    recipients = []
    now = datetime.utcnow()
    for m in members:
        user = db.query(models.User).filter(models.User.id == m["user_id"]).first()
        if not user:
            continue
        note = None
        send_to_email = user.email
        if user.ooo_until and user.ooo_until > now and user.delegate_id:
            delegate = db.query(models.User).filter(models.User.id == user.delegate_id).first()
            if delegate and delegate.email:
                send_to_email = delegate.email
                note = f"{user.name} is out of office until {user.ooo_until.strftime('%d %b')} and has delegated approvals to you."
        if not send_to_email:
            continue
        recipients.append((send_to_email, m["user_id"], m.get("is_optional", False), note))

    if not recipients:
        return

    # Resolve {{field}} / derived-formula variables once for this stage's
    # instructions (see template_utils.py). Same engine used by ad-hoc
    # messages in routers/requests.py:send_message.
    instructions = stage_cfg.get("instructions")
    if instructions:
        instructions = render_template(instructions, resolve_template_variables(req, workflow))

    if channel in (models.NotificationChannel.email, models.NotificationChannel.both):
        for email, user_id, is_optional, note in recipients:
            approve_token = create_approval_token(req.id, request_stage.stage_order, "approved", user_id)
            reject_token = create_approval_token(req.id, request_stage.stage_order, "rejected", user_id)
            full_note = note
            if is_optional:
                opt_note = "Your input here is optional — the request can move forward without it."
                full_note = f"{note} {opt_note}" if note else opt_note
            _run_async(
                notification_service.notify_approvers(
                    approver_emails=[email],
                    request=req,
                    stage_name=stage_cfg.get("name"),
                    workflow_name=workflow.name,
                    approve_token=approve_token,
                    reject_token=reject_token,
                    stage_type=stage_cfg.get("type") or "approval",
                    approve_label=stage_cfg.get("approve_label"),
                    reject_label=stage_cfg.get("reject_label"),
                    note=full_note,
                    instructions=instructions,
                )
            )


def _fire_completion_notification(db, req):
    """Notify submitter when request is fully approved or rejected."""
    workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
    submitter = db.query(models.User).filter(models.User.id == req.submitter_id).first()
    if not submitter or not submitter.email or not workflow:
        return

    # Collect all comments from approval actions
    comments = []
    for rs in req.stages:
        stage_name = rs.stage.name if rs.stage else f"Stage {rs.stage_order}"
        for action in rs.actions:
            if action.comment:
                approver_name = action.approver.name if action.approver else "Approver"
                comments.append({
                    "stage": stage_name,
                    "actor": approver_name,
                    "decision": action.decision.value,
                    "comment": action.comment
                })

    channel = workflow.notification_channel if workflow else models.NotificationChannel.email
    if channel in (models.NotificationChannel.email, models.NotificationChannel.both):
        _run_async(
            notification_service.notify_submitter_completed(
                submitter_email=submitter.email,
                request=req,
                workflow_name=workflow.name,
                comments=comments
            )
        )


def _check_stage_completion(db: Session, request_stage: models.RequestStage, req: models.WorkflowRequest):
    """Evaluate voting rule and decide if stage is complete.

    Reads the frozen stage_cfg (workflow_snapshot.get_stage_config) instead
    of the live WorkflowStage/ApproverGroupMember tables, so a stage's
    voting rule and member list can't change out from under a request
    that's already in progress on it.

    Optional group members (is_optional in the snapshot) may still act —
    their decision is recorded for the audit trail — but it is excluded from
    both the approval count and the rejection trigger below, so an optional
    approver alone can neither complete nor block a stage.
    """
    stage_cfg = get_stage_config(db, req, request_stage.stage_order, request_stage.stage_id)
    if not stage_cfg:
        return

    actions = (
        db.query(models.ApprovalAction)
        .filter(models.ApprovalAction.request_stage_id == request_stage.id)
        .all()
    )

    members = stage_cfg.get("members", [])
    optional_member_ids = {m["user_id"] for m in members if m.get("is_optional")}
    required_member_count = sum(1 for m in members if not m.get("is_optional"))

    # Admin overrides and required members are "required_actions"; an optional
    # member's action is excluded from completion/rejection math entirely.
    required_actions = [a for a in actions if a.approver_id not in optional_member_ids]
    approved_count = sum(1 for a in required_actions if a.decision == models.ApprovalDecision.approved)
    rejected_count = sum(1 for a in required_actions if a.decision == models.ApprovalDecision.rejected)

    group_members = required_member_count

    now = datetime.utcnow()

    # 1. Handle rejection
    if rejected_count >= 1:
        workflow = db.query(models.Workflow).filter(models.Workflow.id == req.workflow_id).first()
        behavior = workflow.rejection_behavior if workflow else models.RejectionBehavior.stop

        if behavior == models.RejectionBehavior.stop:
            request_stage.status = models.RequestStatus.rejected
            req.status = models.RequestStatus.rejected
            req.resolved_at = now
            return
        elif behavior == models.RejectionBehavior.restart:
            for rs in req.stages:
                rs.status = models.RequestStatus.pending
                rs.started_at = None
                rs.completed_at = None
            req.current_stage = -1
            _advance_request(db, req)
            return
        elif behavior == models.RejectionBehavior.escalate:
            request_stage.status = models.RequestStatus.escalated
            req.status = models.RequestStatus.escalated
            return

    # 2. Approval logic
    completed = False
    voting_rule = stage_cfg.get("voting_rule")
    if voting_rule == models.VotingRule.any.value:
        if approved_count >= 1:
            completed = True
            request_stage.status = models.RequestStatus.approved

    elif voting_rule == models.VotingRule.all.value:
        if approved_count >= group_members:
            completed = True
            request_stage.status = models.RequestStatus.approved

    elif voting_rule == models.VotingRule.sequential.value:
        if required_actions:
            last = sorted(required_actions, key=lambda a: a.acted_at)[-1]
            if last.decision == models.ApprovalDecision.rejected:
                request_stage.status = models.RequestStatus.rejected
                req.status = models.RequestStatus.rejected
                req.resolved_at = now
                return
            # Sequential completes only when ALL required members have approved
            if approved_count >= group_members:
                completed = True
                request_stage.status = models.RequestStatus.approved

    if completed:
        request_stage.completed_at = now
        # For parallel-group stages, current_stage tracks the lowest order in the
        # group. We pass the completing stage so _advance_request can identify
        # the parallel_group and check if all siblings are done.
        # Temporarily set current_stage to this stage's order so _advance_request
        # can read its parallel_group config.
        saved_current = req.current_stage
        req.current_stage = request_stage.stage_order
        _advance_request(db, req)
        # If _advance_request decided NOT to advance (siblings still pending),
        # it returns without changing current_stage — restore the saved value
        # so current_stage stays at the parallel group's minimum order.
        if req.status == models.RequestStatus.pending and req.current_stage == request_stage.stage_order:
            req.current_stage = saved_current


@router.post("/", response_model=ApprovalActionOut)
def take_action(
    payload: ApprovalActionCreate,
    user_id: int = Query(...),
    db: Session = Depends(get_db),
):
    current_user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not current_user:
        raise HTTPException(404, "User not found")
    req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == payload.request_id).first()
    if not req:
        raise HTTPException(404, "Request not found")
    if req.status != models.RequestStatus.pending:
        raise HTTPException(400, f"Request is already {req.status.value}")

    active_stage = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.request_id == req.id,
            models.RequestStage.stage_order == req.current_stage,
            models.RequestStage.status == models.RequestStatus.pending
        )
        .first()
    )

    # Parallel groups: the caller may belong to a sibling stage whose order
    # differs from req.current_stage (e.g. a2 acting on stage-order=2 while
    # req.current_stage=1). Always search all started-pending stages for a
    # stage this user is actually a member of, even when a stage at
    # req.current_stage was found (it may belong to a different approver).
    if current_user.role not in (models.UserRole.admin,):
        started_pending = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req.id,
                models.RequestStage.status == models.RequestStatus.pending,
                models.RequestStage.started_at.isnot(None),
            )
            .order_by(models.RequestStage.stage_order)
            .all()
        )
        for sp in started_pending:
            sp_cfg = get_stage_config(db, req, sp.stage_order, sp.stage_id)
            if sp_cfg and current_user.id in snapshot_member_ids(sp_cfg):
                active_stage = sp
                break

        # No started-pending stage found that this user belongs to.
        # Check if they already acted on a now-completed stage (e.g. duplicate
        # action on a parallel stage that just finished) — return 400 not 403.
        if not active_stage:
            already_acted = (
                db.query(models.ApprovalAction)
                .join(
                    models.RequestStage,
                    models.ApprovalAction.request_stage_id == models.RequestStage.id,
                )
                .filter(
                    models.RequestStage.request_id == req.id,
                    models.ApprovalAction.approver_id == current_user.id,
                )
                .first()
            )
            if already_acted:
                raise HTTPException(400, "You have already acted on this stage")
    elif not active_stage:
        # Admin fallback: find any started pending stage
        started_pending = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req.id,
                models.RequestStage.status == models.RequestStatus.pending,
                models.RequestStage.started_at.isnot(None),
            )
            .order_by(models.RequestStage.stage_order)
            .all()
        )
        if started_pending:
            active_stage = started_pending[0]

    if not active_stage:
        # Self-heal fallback for legacy requests without snapshots
        active_stage = (
            db.query(models.RequestStage)
            .filter(
                models.RequestStage.request_id == req.id,
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

    # Sequential check: is it this user's turn?
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

    # Verify approver is in the group (per the frozen stage_cfg) — or is
    # standing in as an OOO member's delegate
    acting_for_id = None
    ooo_principal = None
    if current_user.role not in (models.UserRole.admin,):
        if current_user.id not in members_by_id:
            now_check = datetime.utcnow()
            ooo_principal = (
                db.query(models.User)
                .filter(
                    models.User.id.in_(members_by_id),
                    models.User.delegate_id == current_user.id,
                    models.User.ooo_until.isnot(None),
                    models.User.ooo_until > now_check,
                )
                .first()
            ) if members_by_id else None
            if not ooo_principal:
                raise HTTPException(403, "You are not in the approver group for this stage")
            acting_for_id = ooo_principal.id

    # The slot this action counts against: the OOO principal if we're a stand-in,
    # otherwise the acting user themselves.
    approver_id = acting_for_id or current_user.id

    # Prevent duplicate action against that slot
    existing = (
        db.query(models.ApprovalAction)
        .filter(
            models.ApprovalAction.request_stage_id == active_stage.id,
            models.ApprovalAction.approver_id.in_(
                {approver_id, current_user.id}
            )
        )
        .first()
    )
    if existing:
        raise HTTPException(400, "You have already acted on this stage")

    delegated_to = None
    if payload.decision == models.ApprovalDecision.delegated:
        if not payload.delegated_to_id:
            raise HTTPException(400, "delegated_to_id required for delegation")
        delegated_to = payload.delegated_to_id

    action = models.ApprovalAction(
        request_stage_id=active_stage.id,
        approver_id=approver_id,
        decision=payload.decision,
        comment=payload.comment,
        document_name=payload.document_name,
        document_url=payload.document_url,
        delegated_to_id=payload.delegated_to_id if payload.decision == models.ApprovalDecision.delegated else None,
    )
    db.add(action)
    db.flush()

    verb = payload.decision.value
    detail = f"Stage {req.current_stage} {verb} by {current_user.name}"
    if acting_for_id:
        detail += f" on behalf of {ooo_principal.name} (out of office)"
    if payload.document_url:
        detail += f" with document attached ({payload.document_name or payload.document_url})"
    detail += f". {payload.comment or ''}"
    db.add(models.ActivityLog(
        request_id=req.id,
        user_id=current_user.id,
        action=verb,
        detail=detail,
        stage_order=req.current_stage
    ))

    _check_stage_completion(db, active_stage, req)
    db.commit()
    db.refresh(action)
    return action


@router.get("/pending")
def my_pending(user_id: int = Query(...), db: Session = Depends(get_db)):
    """Return request IDs where the current user has a pending action."""
    current_user = db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()
    if not current_user:
        raise HTTPException(404, "User not found")

    if current_user.role == models.UserRole.admin:
        pending_requests = (
            db.query(models.WorkflowRequest.id)
            .filter(models.WorkflowRequest.status == models.RequestStatus.pending)
            .all()
        )
        return [r.id for r in pending_requests]

    pending_stages = (
        db.query(models.RequestStage)
        .filter(
            models.RequestStage.status == models.RequestStatus.pending,
            models.RequestStage.started_at.isnot(None),
        )
        .all()
    )

    acted_stage_ids = {
        a.request_stage_id for a in
        db.query(models.ApprovalAction)
        .filter(models.ApprovalAction.approver_id == current_user.id)
        .all()
    }

    now = datetime.utcnow()

    # Membership is checked per-request via the frozen stage_cfg (snapshot),
    # not via a live group-membership join, so this reflects who was actually
    # an approver on each request's stage as submitted. A stage also shows up
    # here if current_user is standing in as the delegate for an OOO member
    # of that stage's group — even if current_user themselves isn't a member
    # — mirroring the OOO stand-in logic in take_action/_fire_stage_notification.
    result = set()
    for ps in pending_stages:
        if ps.id in acted_stage_ids:
            continue
        req = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.id == ps.request_id).first()
        if not req:
            continue
        stage_cfg = get_stage_config(db, req, ps.stage_order, ps.stage_id)
        if not stage_cfg:
            continue
        members = snapshot_member_ids(stage_cfg)
        if current_user.id in members:
            result.add(ps.request_id)
            continue
        if not members:
            continue
        ooo_principal = (
            db.query(models.User)
            .filter(
                models.User.id.in_(members),
                models.User.delegate_id == current_user.id,
                models.User.ooo_until.isnot(None),
                models.User.ooo_until > now,
            )
            .first()
        )
        if not ooo_principal:
            continue
        principal_acted = (
            db.query(models.ApprovalAction)
            .filter(
                models.ApprovalAction.request_stage_id == ps.id,
                models.ApprovalAction.approver_id == ooo_principal.id,
            )
            .first()
        )
        if not principal_acted:
            result.add(ps.request_id)

    return list(result)
