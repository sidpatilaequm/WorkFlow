from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
from models import (
    UserRole, WorkflowType, VotingRule, RequestStatus,
    ApprovalDecision, RejectionBehavior, NotificationChannel
)


# ─── Auth ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: UserRole = UserRole.submitter
    department: Optional[str] = None

class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: UserRole
    department: Optional[str]
    is_active: bool
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ─── Approver Groups ──────────────────────────────────────────────────────────

class ApproverGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    member_ids: List[int] = []

class ApproverGroupOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    class Config:
        from_attributes = True


# ─── Workflow Stages ──────────────────────────────────────────────────────────

class StageCreate(BaseModel):
    name: str
    type: WorkflowType
    order: int
    approver_group_id: int
    sla_hours: int = 48
    voting_rule: VotingRule = VotingRule.any
    condition_field: Optional[str] = None
    condition_op: Optional[str] = None
    condition_value: Optional[str] = None

class StageOut(BaseModel):
    id: int
    name: str
    type: WorkflowType
    order: int
    approver_group_id: int
    sla_hours: int
    voting_rule: VotingRule
    condition_field: Optional[str]
    condition_op: Optional[str]
    condition_value: Optional[str]
    class Config:
        from_attributes = True


# ─── Workflows ────────────────────────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    name: str
    description: Optional[str] = None
    type: WorkflowType
    folder_trigger: Optional[str] = None
    escalation_hours: int = 24
    rejection_behavior: RejectionBehavior = RejectionBehavior.stop
    notification_channel: NotificationChannel = NotificationChannel.email
    auto_approve_hours: Optional[int] = None
    amount_threshold: Optional[float] = None
    stages: List[StageCreate] = []

class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    escalation_hours: Optional[int] = None
    rejection_behavior: Optional[RejectionBehavior] = None
    notification_channel: Optional[NotificationChannel] = None
    auto_approve_hours: Optional[int] = None

class WorkflowOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    type: WorkflowType
    folder_trigger: Optional[str]
    is_active: bool
    escalation_hours: int
    rejection_behavior: RejectionBehavior
    notification_channel: NotificationChannel
    auto_approve_hours: Optional[int]
    created_at: datetime
    stages: List[StageOut] = []
    class Config:
        from_attributes = True


# ─── Requests ─────────────────────────────────────────────────────────────────

class RequestCreate(BaseModel):
    title: str
    description: Optional[str] = None
    document_name: Optional[str] = None
    document_url: Optional[str] = None
    amount: Optional[float] = None
    department: Optional[str] = None
    request_type: Optional[str] = None
    workflow_id: int

class RequestStageOut(BaseModel):
    id: int
    stage_order: int
    status: RequestStatus
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    sla_deadline: Optional[datetime]
    is_sla_breached: bool
    class Config:
        from_attributes = True

class RequestOut(BaseModel):
    id: int
    title: str
    description: Optional[str]
    document_name: Optional[str]
    document_url: Optional[str]
    amount: Optional[float]
    department: Optional[str]
    workflow_id: int
    submitter_id: int
    status: RequestStatus
    current_stage: int
    submitted_at: datetime
    resolved_at: Optional[datetime]
    sla_deadline: Optional[datetime]
    stages: List[RequestStageOut] = []
    class Config:
        from_attributes = True


# ─── Approvals ────────────────────────────────────────────────────────────────

class ApprovalActionCreate(BaseModel):
    request_id: int
    decision: ApprovalDecision
    comment: Optional[str] = None
    delegated_to_id: Optional[int] = None

class ApprovalActionOut(BaseModel):
    id: int
    request_stage_id: int
    approver_id: int
    decision: ApprovalDecision
    comment: Optional[str]
    acted_at: datetime
    class Config:
        from_attributes = True


# ─── Activity Log ─────────────────────────────────────────────────────────────

class ActivityLogOut(BaseModel):
    id: int
    request_id: int
    action: str
    detail: Optional[str]
    created_at: datetime
    user: Optional[UserOut]
    class Config:
        from_attributes = True


# ─── Analytics ────────────────────────────────────────────────────────────────

class AnalyticsSummary(BaseModel):
    total_requests: int
    pending: int
    approved: int
    rejected: int
    escalated: int
    approval_rate: float
    avg_resolution_hours: float
    sla_breaches: int
    recent_activity: List[ActivityLogOut] = []
