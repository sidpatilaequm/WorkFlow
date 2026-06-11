from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from schemas import AnalyticsSummary
from auth_utils import get_current_user
import models

router = APIRouter()

@router.get("/summary", response_model=AnalyticsSummary)
def analytics_summary(db: Session = Depends(get_db), _=Depends(get_current_user)):
    total   = db.query(models.WorkflowRequest).count()
    pending  = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.status == "pending").count()
    approved = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.status == "approved").count()
    rejected = db.query(models.WorkflowRequest).filter(models.WorkflowRequest.status == "rejected").count()
    escalated= db.query(models.WorkflowRequest).filter(models.WorkflowRequest.status == "escalated").count()

    approval_rate = round((approved / total * 100) if total > 0 else 0, 1)

    # Average resolution time in hours
    resolved = (
        db.query(models.WorkflowRequest)
        .filter(models.WorkflowRequest.resolved_at.isnot(None))
        .all()
    )
    if resolved:
        avg_hrs = sum(
            (r.resolved_at - r.submitted_at).total_seconds() / 3600
            for r in resolved
        ) / len(resolved)
    else:
        avg_hrs = 0.0

    sla_breaches = db.query(models.RequestStage).filter(models.RequestStage.is_sla_breached == True).count()

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