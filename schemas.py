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
    firstName: Optional[str] = None
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


# --- BUDGET SCHEMAS ---

from datetime import date, datetime
from typing import Optional, List, Any
from pydantic import BaseModel

# ── Organisation ──────────────────────────────────────────────────────────────
class OrganisationOut(BaseModel):
    org_code: str
    name: str
    base_currency: str
    fiscal_year: str

    class Config:
        from_attributes = True


# ── Department ────────────────────────────────────────────────────────────────
class DepartmentCreate(BaseModel):
    name: str
    org_code: str
    wbs: str

class DepartmentOut(BaseModel):
    dept_code: str
    name: str
    org_code: str
    wbs: str

    class Config:
        from_attributes = True


# ── Project ───────────────────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    dept_code: str
    wbs: str

class ProjectOut(BaseModel):
    project_code: str
    name: str
    dept_code: str
    wbs: str

    class Config:
        from_attributes = True


# ── CostType ──────────────────────────────────────────────────────────────────
class CostTypeOut(BaseModel):
    cost_type_code: str
    name: str
    tag: str

    class Config:
        from_attributes = True


# ── Status ────────────────────────────────────────────────────────────────────
class StatusOut(BaseModel):
    status_code: str
    name: str
    sort_order: int

    class Config:
        from_attributes = True


# ── Employee ──────────────────────────────────────────────────────────────────
class EmployeeCreate(BaseModel):
    name: str
    title: Optional[str] = None
    dept_code: Optional[str] = None
    manager_code: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    admin_email: Optional[EmailStr] = None

class EmployeeOut(BaseModel):
    employee_code: str
    name: str
    title: Optional[str]
    dept_code: Optional[str]
    manager_code: Optional[str]
    email: Optional[str] = None
    admin_email: Optional[str] = None

    class Config:
        from_attributes = True


# ── Phase (monthly) ───────────────────────────────────────────────────────────
class PhaseOut(BaseModel):
    period_no: int
    alloc: int
    pr: int
    po: int
    cons: int

    class Config:
        from_attributes = True

class PhaseUpdate(BaseModel):
    period_no: int
    alloc: int
    pr: int
    po: int
    cons: int


# ── Change requests (governed budget movement) ────────────────────────────────
class ChangeRequestCreate(BaseModel):
    request_type: str          # months | carry | pull | transfer
    a_code: str                # source activity_code
    b_code: Optional[str] = None   # target activity_code (transfer only)
    period_from: int           # 1-12
    period_to: int             # 1-12 (== period_from for transfer)
    amount: int
    reason: Optional[str] = None
    requested_by: Optional[str] = None

class ChangeRequestDecide(BaseModel):
    approve: bool
    decided_by: Optional[str] = None

class ChangeRequestOut(BaseModel):
    id: str
    request_type: str
    a_code: str
    b_code: Optional[str]
    period_from: int
    period_to: int
    amount: int
    reason: Optional[str]
    requested_by: Optional[str]
    decided_by: Optional[str]
    status: str
    created_at: datetime
    decided_at: Optional[datetime]

    # enriched for display, filled in by the route handler
    a_name: Optional[str] = None
    b_name: Optional[str] = None

    class Config:
        from_attributes = True


# ── SubActivity ───────────────────────────────────────────────────────────────
class SubActivityCreate(BaseModel):
    name: str
    parent_activity_code: str
    level: int = 1
    cost_type_code: Optional[str] = None
    employee_code: Optional[str] = None
    status_code: Optional[str] = None
    wbs: str
    is_leaf: int = 1
    allocated: int = 0
    approved: int = 0
    pr: int = 0
    po: int = 0
    invoiced: int = 0

class SubActivityUpdate(BaseModel):
    allocated: Optional[int] = None
    approved: Optional[int] = None
    pr: Optional[int] = None
    po: Optional[int] = None
    invoiced: Optional[int] = None
    status_code: Optional[str] = None
    employee_code: Optional[str] = None

class SubActivityOut(BaseModel):
    subactivity_code: str
    name: str
    parent_activity_code: str
    level: int
    cost_type_code: Optional[str]
    employee_code: Optional[str]
    status_code: Optional[str]
    wbs: str
    is_leaf: int
    allocated: int
    approved: int = 0
    pr: int
    po: int
    invoiced: int

    cost_type_tag: Optional[str] = None
    status_name: Optional[str] = None
    employee_name: Optional[str] = None
    phases: List[PhaseOut] = []

    class Config:
        from_attributes = True


# ── Activity ──────────────────────────────────────────────────────────────────
class ActivityCreate(BaseModel):
    name: str
    project_code: str
    cost_type_code: Optional[str] = None
    employee_code: Optional[str] = None
    status_code: Optional[str] = None
    wbs: str
    is_leaf: int = 1
    allocated: int = 0
    approved: int = 0
    pr: int = 0
    po: int = 0
    invoiced: int = 0

class ActivityUpdate(BaseModel):
    allocated: Optional[int] = None
    approved: Optional[int] = None
    pr: Optional[int] = None
    po: Optional[int] = None
    invoiced: Optional[int] = None
    status_code: Optional[str] = None
    employee_code: Optional[str] = None

class ActivityOut(BaseModel):
    activity_code: str
    name: str
    project_code: str
    cost_type_code: Optional[str]
    employee_code: Optional[str]
    status_code: Optional[str]
    wbs: str
    is_leaf: int
    allocated: int
    approved: int = 0
    pr: int
    po: int
    invoiced: int

    cost_type_tag: Optional[str] = None
    status_name: Optional[str] = None
    employee_name: Optional[str] = None

    sub_activities: List[SubActivityOut] = []
    phases: List[PhaseOut] = []

    class Config:
        from_attributes = True


# ── BudgetVersion ─────────────────────────────────────────────────────────────
class BudgetVersionCreate(BaseModel):
    name: str
    fiscal_year: Optional[str] = None
    created_date: date
    basis: Optional[str] = None
    is_current: int = 0
    is_locked: int = 0

class BudgetVersionOut(BaseModel):
    version_code: str
    name: str
    fiscal_year: Optional[str]
    created_date: date
    basis: Optional[str]
    is_current: int
    is_locked: int

    class Config:
        from_attributes = True


# ── FiscalPeriod ──────────────────────────────────────────────────────────────
class FiscalPeriodOut(BaseModel):
    period_no: int
    period_code: str
    quarter: str
    month: str
    state: str
    fiscal_year: str

    class Config:
        from_attributes = True


# ── Transfer ──────────────────────────────────────────────────────────────────
class TransferCreate(BaseModel):
    transfer_type: str
    from_code: str
    to_code: str
    amount: int
    employee_code: Optional[str] = None
    note: Optional[str] = None
    transfer_date: date

class TransferOut(BaseModel):
    id: str
    transfer_type: str
    from_code: str
    to_code: str
    amount: int
    employee_code: Optional[str]
    note: Optional[str]
    transfer_date: date
    status: str

    class Config:
        from_attributes = True


# ── BudgetUpload ──────────────────────────────────────────────────────────────
class BudgetUploadOut(BaseModel):
    id: str
    filename: str
    fiscal_year: Optional[str]
    rows_inserted: int
    rows_updated: int
    rows_skipped: int
    uploaded_at: datetime
    notes: Optional[str]

    class Config:
        from_attributes = True


# ── Dashboard ─────────────────────────────────────────────────────────────────
class DeptSummary(BaseModel):
    dept_code: str
    dept_name: str
    allocated: int
    pr: int
    po: int
    invoiced: int
    remaining: int

class DashboardOut(BaseModel):
    allocated: int
    pr: int
    po: int
    invoiced: int
    remaining: int
    pr_remaining: int
    po_remaining: int
    opex_total: int
    capex_total: int
    dept_summaries: List[DeptSummary]


# ── Invoice Validation ────────────────────────────────────────────────────────
class InvoiceBudgetCheckReq(BaseModel):
    activity_code: str
    amount_with_tax: float
    amount_without_tax: float

class InvoiceBudgetCheckRes(BaseModel):
    current_month_allocated: int
    current_month_available: int
    current_quarter_allocated: int
    current_quarter_available: int
    covers_with_tax: bool
    covers_without_tax: bool

class BudgetBlockReq(BaseModel):
    activity_code: str
    amount: float



class DepartmentSetHead(BaseModel):
    employee_code: Optional[str] = None

class BudgetUploadDecide(BaseModel):
    approve: bool
    decided_by: Optional[str] = None


# ─── Email templates ────────────────────────────────────────────────────────

class EmailFooterOut(BaseModel):
    id: int
    name: str
    reason_text: str
    legal_line: Optional[str] = None
    class Config:
        from_attributes = True

class EmailFooterUpdate(BaseModel):
    name: Optional[str] = None
    reason_text: Optional[str] = None
    legal_line: Optional[str] = None

class EmailTemplateCreate(BaseModel):
    """Just enough to store a new template row and hand back its id — everything
    else (subject wording, body, footer, ...) is filled in afterward through the
    same PATCH-based editor every other template uses. No process/step/event
    modelling: wiring a mail_key up to an actual send happens in code later,
    the same way VO.2/VCR.1/etc. already do via send_triggered_email(mail_key, ...)."""
    process_key: str
    mail_key: str
    mail_label: str

class EmailTemplateOut(BaseModel):
    id: int
    process_key: str
    mail_key: str
    mail_label: str
    enabled: bool
    from_address: Optional[str] = None
    reply_to: Optional[str] = None
    status_strip_text: Optional[str] = None
    status_strip_tone: Optional[str] = None
    subject: str
    preheader: Optional[str] = None
    heading: str
    intro: Optional[str] = None
    detail_rows: Optional[List[List[str]]] = None
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    outro: Optional[str] = None
    footer_id: Optional[int] = None
    footer_override_reason: Optional[str] = None
    footer_override_legal: Optional[str] = None
    sample_data: Optional[Any] = None
    class Config:
        from_attributes = True

class EmailTemplateUpdate(BaseModel):
    enabled: Optional[bool] = None
    from_address: Optional[str] = None
    reply_to: Optional[str] = None
    status_strip_text: Optional[str] = None
    status_strip_tone: Optional[str] = None
    subject: Optional[str] = None
    preheader: Optional[str] = None
    heading: Optional[str] = None
    intro: Optional[str] = None
    detail_rows: Optional[List[List[str]]] = None
    cta_label: Optional[str] = None
    cta_url: Optional[str] = None
    outro: Optional[str] = None
    footer_id: Optional[int] = None
    footer_override_reason: Optional[str] = None
    footer_override_legal: Optional[str] = None
    sample_data: Optional[dict] = None

class EmailTemplatePreviewOut(BaseModel):
    subject: str
    html: str
    text: str

class EmailTemplateTestSend(BaseModel):
    to_email: EmailStr

class EmailTemplateTriggerRequest(BaseModel):
    to_email: EmailStr
    variables: dict = {}
