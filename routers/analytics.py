from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime, timedelta
from database import get_db
from schemas import AnalyticsSummary, NotificationReport, WorkflowNotificationRow
import models

router = APIRouter()


@router.get("/summary", response_model=AnalyticsSummary)
def analytics_summary(
    user_id: int = Query(...),
    days: int = Query(30, ge=1, le=365),
    workflow_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(days=days)

    q = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.submitted_at >= since)
    if workflow_id:
        q = q.filter(models.WorkflowRequest.workflow_id == workflow_id)

    all_requests = q.all()
    total    = len(all_requests)
    pending  = sum(1 for r in all_requests if r.status == models.RequestStatus.pending)
    approved = sum(1 for r in all_requests if r.status == models.RequestStatus.approved)
    rejected = sum(1 for r in all_requests if r.status == models.RequestStatus.rejected)
    escalated = sum(1 for r in all_requests if r.status == models.RequestStatus.escalated)

    approval_rate = round((approved / total * 100) if total > 0 else 0.0, 1)

    resolved = [r for r in all_requests if r.resolved_at is not None]
    avg_hrs = 0.0
    if resolved:
        avg_hrs = sum(
            (r.resolved_at - r.submitted_at).total_seconds() / 3600
            for r in resolved
        ) / len(resolved)

    sla_breaches = (
        db.query(models.RequestStage)
        .filter(models.RequestStage.is_sla_breached == True)
        .count()
    )

    recent_activity = (
        db.query(models.ActivityLog)
        .order_by(models.ActivityLog.created_at.desc())
        .limit(20)
        .all()
    )

    return AnalyticsSummary(
        total_requests=total,
        pending=pending,
        approved=approved,
        rejected=rejected,
        escalated=escalated,
        approval_rate=approval_rate,
        avg_resolution_hours=round(avg_hrs, 1),
        sla_breaches=sla_breaches,
        recent_activity=recent_activity,
    )


@router.get("/by-workflow")
def by_workflow(
    user_id: int = Query(...),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Breakdown of approval metrics per workflow template."""
    since = datetime.utcnow() - timedelta(days=days)

    workflows = db.query(models.Workflow).all()
    result = []
    for wf in workflows:
        reqs = (
            db.query(models.WorkflowRequest)
            .filter(
                models.WorkflowRequest.workflow_id == wf.id,
                models.WorkflowRequest.submitted_at >= since,
            )
            .all()
        )
        total = len(reqs)
        if total == 0:
            continue
        approved = sum(1 for r in reqs if r.status == models.RequestStatus.approved)
        rejected = sum(1 for r in reqs if r.status == models.RequestStatus.rejected)
        result.append({
            "workflow_id": wf.id,
            "workflow": wf.name,
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "approval_rate_pct": round(approved / total * 100, 1),
        })

    result.sort(key=lambda x: x["total"], reverse=True)
    return result


@router.get("/approver-performance")
def approver_performance(
    user_id: int = Query(...),
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """Per-approver decision counts and average response time."""
    since = datetime.utcnow() - timedelta(days=days)

    actions = (
        db.query(models.ApprovalAction)
        .filter(models.ApprovalAction.acted_at >= since)
        .all()
    )

    by_approver: dict[int, dict] = {}
    for action in actions:
        uid = action.approver_id
        if uid not in by_approver:
            user = db.query(models.User).filter(models.User.id == uid).first()
            by_approver[uid] = {
                "approver_id": uid,
                "approver": user.name if user else "Unknown",
                "email": user.email if user else "",
                "total_decisions": 0,
                "approved": 0,
                "rejected": 0,
                "delegated": 0,
                "response_seconds": [],
            }
        entry = by_approver[uid]
        entry["total_decisions"] += 1
        if action.decision == models.ApprovalDecision.approved:
            entry["approved"] += 1
        elif action.decision == models.ApprovalDecision.rejected:
            entry["rejected"] += 1
        elif action.decision == models.ApprovalDecision.delegated:
            entry["delegated"] += 1

        rs = db.query(models.RequestStage).filter(
            models.RequestStage.id == action.request_stage_id
        ).first()
        if rs and rs.started_at and action.acted_at:
            delta = (action.acted_at - rs.started_at).total_seconds()
            if delta >= 0:
                entry["response_seconds"].append(delta)

    result = []
    for entry in by_approver.values():
        secs = entry.pop("response_seconds")
        avg_hrs = round(sum(secs) / len(secs) / 3600, 1) if secs else None
        result.append({**entry, "avg_response_hours": avg_hrs})

    result.sort(key=lambda x: x["total_decisions"], reverse=True)
    return result


@router.get("/activity-feed")
def activity_feed(
    user_id: int = Query(...),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """Recent audit activity with request and actor details."""
    rows = (
        db.query(models.ActivityLog)
        .order_by(models.ActivityLog.created_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for log in rows:
        req = db.query(models.WorkflowRequest).filter(
            models.WorkflowRequest.id == log.request_id
        ).first()
        user = db.query(models.User).filter(models.User.id == log.user_id).first() if log.user_id else None
        result.append({
            "id": log.id,
            "action": log.action,
            "document_name": req.document_name if req else None,
            "request_title": req.title if req else None,
            "actor_name": user.name if user else "System",
            "detail": log.detail,
            "created_at": log.created_at.isoformat(),
        })
    return result


@router.get("/notification-report", response_model=NotificationReport)
def notification_report(
    user_id: int = Query(...),
    days: int = Query(30, ge=1, le=365),
    workflow_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """
    Notification compliance report.

    For every RequestStage that started within the period we classify it as:

      bypassed  — the stage completed (approved/rejected) BEFORE the scheduler
                  ever sent a reminder (no 'reminder_sent' ActivityLog entry
                  exists for that stage).  These approvers acted on their own.

      received_reminders — at least one 'reminder_sent' log exists for the stage.
        └─ still_pending — the stage is still pending (never resolved after reminders).

    Aggregated at the top level and broken down per workflow.
    """
    since = datetime.utcnow() - timedelta(days=days)

    # ── Collect all RequestStages that started in the period ─────────────────
    rs_query = (
        db.query(models.RequestStage)
        .join(models.WorkflowRequest,
              models.RequestStage.request_id == models.WorkflowRequest.id)
        .filter(
            models.RequestStage.started_at >= since,
            models.RequestStage.started_at.isnot(None),
        )
    )
    if workflow_id:
        rs_query = rs_query.filter(
            models.WorkflowRequest.workflow_id == workflow_id
        )

    all_stages = rs_query.all()

    # ── Build a set of request_stage_ids that received at least one reminder ─
    reminded_stage_ids: set[int] = set()
    reminder_logs = (
        db.query(models.ActivityLog)
        .filter(
            models.ActivityLog.action == "reminder_sent",
            models.ActivityLog.created_at >= since,
        )
        .all()
    )
    for log in reminder_logs:
        if log.extra and isinstance(log.extra, dict):
            rs_id = log.extra.get("request_stage_id")
            if rs_id:
                reminded_stage_ids.add(rs_id)

    # ── Per-workflow accumulators ─────────────────────────────────────────────
    # wf_id → { total, bypassed, received, still_pending, name }
    wf_buckets: dict[int, dict] = {}

    total_stages_run   = 0
    total_bypassed     = 0
    total_received     = 0
    total_still_pending = 0

    for rs in all_stages:
        req = db.query(models.WorkflowRequest).filter(
            models.WorkflowRequest.id == rs.request_id
        ).first()
        if not req:
            continue

        wf = db.query(models.Workflow).filter(
            models.Workflow.id == req.workflow_id
        ).first()
        if not wf:
            continue

        wid = wf.id
        if wid not in wf_buckets:
            wf_buckets[wid] = {
                "workflow_id": wid,
                "workflow_name": wf.name,
                "total": 0,
                "bypassed": 0,
                "received": 0,
                "still_pending": 0,
            }

        b = wf_buckets[wid]
        b["total"] += 1
        total_stages_run += 1

        stage_was_reminded = rs.id in reminded_stage_ids

        if stage_was_reminded:
            b["received"] += 1
            total_received += 1
            # Still pending = stage never completed after reminders were sent
            if rs.status == models.RequestStatus.pending:
                b["still_pending"] += 1
                total_still_pending += 1
        else:
            # No reminder was ever sent → approvers acted before the window expired
            b["bypassed"] += 1
            total_bypassed += 1

    # ── Build per-workflow rows ───────────────────────────────────────────────
    by_workflow_rows = []
    for b in sorted(wf_buckets.values(), key=lambda x: x["total"], reverse=True):
        t = b["total"]
        bypass_pct = round(b["bypassed"] / t * 100, 1) if t > 0 else 0.0
        by_workflow_rows.append(WorkflowNotificationRow(
            workflow_id=b["workflow_id"],
            workflow_name=b["workflow_name"],
            total_stages_run=t,
            bypassed_notifications=b["bypassed"],
            received_reminders=b["received"],
            still_pending_after_reminder=b["still_pending"],
            bypass_rate_pct=bypass_pct,
        ))

    overall_bypass_pct = (
        round(total_bypassed / total_stages_run * 100, 1)
        if total_stages_run > 0 else 0.0
    )

    return NotificationReport(
        period_days=days,
        total_stages_run=total_stages_run,
        total_bypassed=total_bypassed,
        total_received_reminders=total_received,
        total_still_pending=total_still_pending,
        overall_bypass_rate_pct=overall_bypass_pct,
        by_workflow=by_workflow_rows,
    )
