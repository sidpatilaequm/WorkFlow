from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime
from models import (
    UserRole, WorkflowType, VotingRule, RequestStatus,
    ApprovalDecision, RejectionBehavior, NotificationChannel
)


# ─── Auth ─────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    firstName: str
    lastName: Optional[str] = None
    email: EmailStr
    password: str
    role: UserRole = UserRole.submitter
    phoneNumber: Optional[str] = None
    designation: Optional[str] = None
    company_id: Optional[int] = None

class UserOut(BaseModel):
    id: int
    firstName: str
    lastName: Optional[str] = None
    name: Optional[str] = None
    email: str
    role: UserRole
    phoneNumber: Optional[str] = None
    designation: Optional[str] = None
    onboardingStatus: Optional[str] = None
    company_id: Optional[int] = None
    is_active: bool
    ooo_until: Optional[datetime] = None
    delegate_id: Optional[int] = None
    class Config:
        from_attributes = True

class OutOfOfficeUpdate(BaseModel):
    """Self-service: mark yourself OOO and name a delegate to receive your approvals."""
    ooo_until: Optional[datetime] = None
    delegate_id: Optional[int] = None

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut

class TokenResponse(BaseModel):
    """Access + refresh token pair (no user object — used by /refresh)."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str


# ─── Approver Groups ──────────────────────────────────────────────────────────

class ApproverGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    member_ids: List[int] = []
    # Members listed here are notified like everyone else, but their decision
    # never counts toward stage completion and never blocks the stage.
    optional_member_ids: List[int] = []

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
    approver_group_id: Optional[int] = None
    sla_hours: int = 48
    voting_rule: VotingRule = VotingRule.any
    # Free-text button overrides. Leave unset to use the stage-type presets
    # (Approve/Reject, Mark Reviewed/Request Changes, etc).
    approve_label: Optional[str] = None
    reject_label: Optional[str] = None
    is_optional: bool = False
    instructions: Optional[str] = None
    # Parallel execution group: stages in the same workflow sharing the same
    # non-null parallel_group integer start simultaneously. All stages in the
    # group must complete before the workflow advances past them.
    # Leave None for serial (one-at-a-time) execution.
    parallel_group: Optional[int] = None
    condition_field: Optional[str] = None
    condition_op: Optional[str] = None
    condition_value: Optional[str] = None

class StageOut(BaseModel):
    id: int
    name: str
    type: WorkflowType
    order: int
    approver_group_id: Optional[int]
    sla_hours: int
    voting_rule: VotingRule
    approve_label: Optional[str] = None
    reject_label: Optional[str] = None
    is_optional: bool
    instructions: Optional[str]
    parallel_group: Optional[int] = None
    condition_field: Optional[str]
    condition_op: Optional[str]
    condition_value: Optional[str]

    class Config:
        from_attributes = True


# ─── Workflows ────────────────────────────────────────────────────────────────

class MessageVariable(BaseModel):
    """Derived value for message templates, e.g. {"name": "tax", "formula": "amount * 0.18"}.
    See template_utils.py for the (arithmetic-only, sandboxed) formula grammar."""
    name: str
    formula: str


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
    # Where /api/requests/action/{token}/redirect sends the browser once an
    # email action resolves. Falls back to FRONTEND_URL/requests/{id}.
    success_redirect_url: Optional[str] = None
    failure_redirect_url: Optional[str] = None
    auto_approve_conditions: Optional[Any] = None
    reminder_after_hours: Optional[int] = None
    reminder_interval_hours: Optional[int] = None
    message_variables: Optional[List[MessageVariable]] = None
    stages: List[StageCreate] = []

class WorkflowUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    escalation_hours: Optional[int] = None
    rejection_behavior: Optional[RejectionBehavior] = None
    notification_channel: Optional[NotificationChannel] = None
    auto_approve_hours: Optional[int] = None
    success_redirect_url: Optional[str] = None
    failure_redirect_url: Optional[str] = None
    amount_threshold: Optional[float] = None
    auto_approve_conditions: Optional[Any] = None
    reminder_after_hours: Optional[int] = None
    reminder_interval_hours: Optional[int] = None
    message_variables: Optional[List[MessageVariable]] = None
    stages: Optional[List[StageCreate]] = None

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
    success_redirect_url: Optional[str] = None
    failure_redirect_url: Optional[str] = None
    amount_threshold: Optional[float]
    auto_approve_conditions: Optional[Any]
    reminder_after_hours: Optional[int]
    reminder_interval_hours: Optional[int]
    message_variables: Optional[List[MessageVariable]] = None
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
    document_type: Optional[str] = None
    folder_path: Optional[str] = None
    amount: Optional[float] = None
    department: Optional[str] = None
    request_type: Optional[str] = None
    request_metadata: Optional[Any] = None
    workflow_id: int

class ApprovalActionDetail(BaseModel):
    id: int
    approver_id: Optional[int] = None
    approver_name: Optional[str] = None
    decision: ApprovalDecision
    comment: Optional[str] = None
    document_name: Optional[str] = None
    document_url: Optional[str] = None
    acted_at: datetime
    request_stage_id: int
    class Config:
        from_attributes = True

class RequestStageOut(BaseModel):
    id: int
    stage_id: Optional[int] = None
    stage_order: int
    stage_name: Optional[str] = None
    stage_type: Optional[str] = None
    group_name: Optional[str] = None
    voting_rule: Optional[str] = None
    approve_label: Optional[str] = None
    reject_label: Optional[str] = None
    is_optional: bool = False
    parallel_group: Optional[int] = None
    status: RequestStatus
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    sla_deadline: Optional[datetime]
    is_sla_breached: bool
    actions: List[ApprovalActionDetail] = []
    class Config:
        from_attributes = True

class RequestOut(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    document_name: Optional[str] = None
    document_url: Optional[str] = None
    document_type: Optional[str] = None
    folder_path: Optional[str] = None
    amount: Optional[float] = None
    department: Optional[str] = None
    request_type: Optional[str] = None
    request_metadata: Optional[Any] = None
    workflow_id: Optional[int] = None
    workflow_name: Optional[str] = None
    submitter_id: Optional[int] = None
    submitter_name: Optional[str] = None
    status: RequestStatus
    current_stage: int
    total_stages: int = 0
    submitted_at: datetime
    resolved_at: Optional[datetime] = None
    sla_deadline: Optional[datetime] = None
    stages: List[RequestStageOut] = []
    history: List[dict] = []
    pending_group_name: Optional[str] = None
    # Frozen stage/approver-group config as of submission time (see
    # routers/requests.py:_build_workflow_snapshot). Reflects the workflow
    # "as submitted", independent of later edits to the live workflow.
    workflow_snapshot: Optional[Any] = None
    class Config:
        from_attributes = True


# ─── Approvals ────────────────────────────────────────────────────────────────

class ApprovalActionCreate(BaseModel):
    request_id: int
    decision: ApprovalDecision
    comment: Optional[str] = None
    delegated_to_id: Optional[int] = None
    # Set after calling POST /api/requests/upload, to attach a document
    # (signed copy, rejection memo, etc) to this specific decision.
    document_name: Optional[str] = None
    document_url: Optional[str] = None

class ApprovalActionOut(BaseModel):
    id: int
    request_stage_id: int
    approver_id: int
    decision: ApprovalDecision
    comment: Optional[str]
    document_name: Optional[str] = None
    document_url: Optional[str] = None
    acted_at: datetime
    class Config:
        from_attributes = True


# ─── Activity Log ─────────────────────────────────────────────────────────────

class ActivityLogOut(BaseModel):
    id: int
    request_id: Optional[int] = None
    action: str
    detail: Optional[str]
    stage_order: Optional[int] = None
    extra: Optional[Any] = None
    created_at: datetime
    user: Optional[UserOut]
    class Config:
        from_attributes = True


# ─── Analytics ────────────────────────────────────────────────────────────────

class ManualMessageCreate(BaseModel):
    """Send a one-off message tied to a request, independent of any stage."""
    subject: Optional[str] = None
    message: str
    to: str = "submitter"  # "submitter" | "current_approvers" | "custom"
    custom_emails: Optional[List[EmailStr]] = None
    # If set, the message is re-sent every reminder_interval_hours for as
    # long as the request stays "pending" (i.e. the status it's meant to
    # prompt hasn't been achieved yet), up to max_reminders times (or
    # indefinitely until resolved if max_reminders is None).
    reminder_interval_hours: Optional[int] = None
    max_reminders: Optional[int] = None


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


class WorkflowNotificationRow(BaseModel):
    """Per-workflow breakdown for the notification report."""
    workflow_id: int
    workflow_name: str
    # Total request-stages that ran through this workflow in the period
    total_stages_run: int
    # Stages where all approvers acted before any reminder was sent
    bypassed_notifications: int
    # Stages that received at least one reminder
    received_reminders: int
    # Of those that received reminders: how many are still unresolved
    still_pending_after_reminder: int
    # Percentage of stages that bypassed (acted without needing a reminder)
    bypass_rate_pct: float


class NotificationReport(BaseModel):
    """Top-level notification compliance report."""
    period_days: int
    total_stages_run: int
    total_bypassed: int                 # resolved without any reminder
    total_received_reminders: int       # got at least one reminder
    total_still_pending: int            # got reminder and still unresolved
    overall_bypass_rate_pct: float
    by_workflow: List[WorkflowNotificationRow] = []


# ─── Standalone Messages (Messaging #4) ──────────────────────────────────────

class StandaloneMessageCreate(BaseModel):
    """Fire a notification independent of any workflow request.

    `context` is a flat key-value dict whose keys can be referenced as
    {{key}} placeholders in subject/message, e.g. {"vendor": "Infosys",
    "amount": 250000} lets you write "Invoice from {{vendor}} for {{amount}}".
    If reminder_interval_hours is set, the message is re-sent on that cadence
    until max_reminders is reached (or indefinitely when max_reminders is None)
    or the record is manually deactivated via PATCH /api/messages/{id}/deactivate.
    """
    to_emails: List[EmailStr]
    subject: Optional[str] = None
    message: str
    context: Optional[dict] = None
    reminder_interval_hours: Optional[int] = None
    max_reminders: Optional[int] = None


class StandaloneMessageOut(BaseModel):
    id: int
    sender_id: Optional[int]
    to_emails: List[str]
    subject: Optional[str]
    message: str
    context: Optional[Any]
    reminder_interval_hours: Optional[int]
    max_reminders: Optional[int]
    reminders_sent: int
    last_sent_at: Optional[datetime]
    is_active: bool
    created_at: datetime
    class Config:
        from_attributes = True
