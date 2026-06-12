from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional
from datetime import datetime, timedelta
from database import get_db
from schemas import AnalyticsSummary
from auth_utils import get_current_user
import models

router = APIRouter()


@router.get("/summary", response_model=AnalyticsSummary)
def analytics_summary(
    days: int = Query(30, ge=1, le=365),
    workflow_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
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
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
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
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Per-approver decision counts and average response time."""
    since = datetime.utcnow() - timedelta(days=days)

    actions = (
        db.query(models.ApprovalAction)
        .filter(models.ApprovalAction.acted_at >= since)
        .all()
    )

    # Group by approver
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

        # Use stage started_at as the "assigned_at" proxy
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
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
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
