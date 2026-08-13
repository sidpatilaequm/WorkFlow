from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
from models import WorkflowRequest
from pydantic import BaseModel
from services.notification import notification_service

router = APIRouter()

@router.get("/pr-numbers", response_model=List[str])
def get_pr_numbers(db: Session = Depends(get_db)):
    """Return a list of all PR numbers (extracted from titles) for selection."""
    prs = db.query(WorkflowRequest.title).filter(WorkflowRequest.title != None).all()
    # Extract PR number from title (e.g. "PR-2026-0012 Requested" -> "PR-2026-0012")
    pr_numbers = []
    import re
    for pr in prs:
        title = pr[0]
        match = re.search(r'(PR-\d{4}-\d+)', title)
        if match:
            pr_numbers.append(match.group(1))
    return list(set(pr_numbers))

class NotificationPayload(BaseModel):
    email: str
    pr_number: str
    status: Optional[str] = None
    created_by: Optional[str] = None
    created_date: Optional[str] = None
    items_count: Optional[int] = None

@router.post("/send-notification")
async def send_notification(payload: NotificationPayload, db: Session = Depends(get_db)):
    """Send email notification about a PR to the specified email."""
    # Find PR
    pr = db.query(WorkflowRequest).filter(WorkflowRequest.title.like(f"%{payload.pr_number}%")).first()
    
    status = payload.status or (pr.status.value if pr and hasattr(pr.status, 'value') else (pr.status if pr else "Unknown"))
    description = pr.description if pr else f"Standard Purchase Requisition {payload.pr_number}"
    created_by = payload.created_by or (pr.submitter.name if pr and pr.submitter else "System")
    created_date = payload.created_date or (pr.submitted_at.strftime("%d %b %Y") if pr and pr.submitted_at else "")
    items_count = payload.items_count or 1

    subject = f"Purchase Requisition {payload.pr_number} Created"
    html_body = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
        <h2 style="color: #198754; margin-top: 0;">Purchase Requisition Notification</h2>
        <p>A Purchase Requisition has been created with the following details:</p>
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
            <tr style="background-color: #f8f9fa;">
                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold; width: 30%;">PR Number</td>
                <td style="padding: 10px; border: 1px solid #dee2e6; color: #198754; font-weight: bold;">{payload.pr_number}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Status</td>
                <td style="padding: 10px; border: 1px solid #dee2e6; text-transform: uppercase;">{status}</td>
            </tr>
            <tr style="background-color: #f8f9fa;">
                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Created By</td>
                <td style="padding: 10px; border: 1px solid #dee2e6;">{created_by}</td>
            </tr>
            <tr>
                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Created Date</td>
                <td style="padding: 10px; border: 1px solid #dee2e6;">{created_date}</td>
            </tr>
            <tr style="background-color: #f8f9fa;">
                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Line Items</td>
                <td style="padding: 10px; border: 1px solid #dee2e6;">{items_count} item(s)</td>
            </tr>
            <tr>
                <td style="padding: 10px; border: 1px solid #dee2e6; font-weight: bold;">Description</td>
                <td style="padding: 10px; border: 1px solid #dee2e6;">{description or ''}</td>
            </tr>
        </table>
        <p style="color: #6c757d; font-size: 14px; margin-bottom: 0;">This is an automated notification from the Document Workflow Engine.</p>
    </div>
    """
    
    # Send email
    sent = await notification_service.send_email(to=[payload.email], subject=subject, html_body=html_body)
    if not sent:
        raise HTTPException(status_code=500, detail="Failed to send email")
    return {"detail": "Notification sent"}
