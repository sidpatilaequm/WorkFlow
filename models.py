# pyrefly: ignore [missing-import]
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, DateTime, Boolean,
    Float, ForeignKey, Enum as SAEnum, JSON
)
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import relationship
# pyrefly: ignore [missing-import]
from sqlalchemy.sql import func
from database import Base
import enum


# ─── Enums ────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    submitter   = "submitter"
    approver    = "approver"
    admin       = "admin"
    SUPER_ADMIN = "SUPER_ADMIN"
    VENDOR      = "VENDOR"
    EMPLOYEE    = "EMPLOYEE"
    PURCHASE_DEPT = "PURCHASE_DEPT"


def is_admin_role(role) -> bool:
    """True for roles that should see/manage every request, not just their own.

    Two distinct values mean "admin" here because `role` mirrors the synced
    `user_details.user_type` column, whose backend_java enum never actually
    produces the literal "admin" — real admin accounts arrive as SUPER_ADMIN.
    """
    return role in (UserRole.admin, UserRole.SUPER_ADMIN)

class WorkflowType(str, enum.Enum):
    approval        = "approval"
    review          = "review"
    acknowledgement = "acknowledgement"
    signature       = "signature"

class VotingRule(str, enum.Enum):
    any        = "any"
    all        = "all"
    sequential = "sequential"

class RequestStatus(str, enum.Enum):
    pending   = "pending"
    approved  = "approved"
    rejected  = "rejected"
    escalated = "escalated"
    cancelled = "cancelled"

class ApprovalDecision(str, enum.Enum):
    approved  = "approved"
    rejected  = "rejected"
    delegated = "delegated"

class RejectionBehavior(str, enum.Enum):
    stop     = "stop"
    restart  = "restart"
    escalate = "escalate"

class NotificationChannel(str, enum.Enum):
    email = "email"
    slack = "slack"
    both  = "both"


# ─── Models ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "user_details"

    id               = Column("user_id", Integer, primary_key=True, index=True)
    super_admin_id   = Column("super_admin_id", Integer, ForeignKey("user_details.user_id"), nullable=True)
    email            = Column(String(150), unique=True, nullable=False, index=True)
    password         = Column("password", String(255), nullable=False)
    firstName        = Column("first_name", String(100))
    lastName         = Column("last_name", String(100))
    phoneNumber      = Column("phone_number", String(50))
    signupDate       = Column("signup_date", String(50))
    designation      = Column("designation", String(100))
    onboardingStatus = Column("onboarding_status", String(100))
    onboardingToken  = Column("onboarding_token", String(255))
    tokenExpiry      = Column("token_expiry", DateTime, nullable=True)
    role             = Column("user_type", SAEnum(UserRole), default=UserRole.submitter)
    company_id       = Column("company_id", Integer, nullable=True)
    employee_code    = Column("employee_code", String(20), ForeignKey("employee.employee_code"), nullable=True)
    dept_code        = Column("dept_code", String(20), ForeignKey("department.dept_code"), nullable=True)
    created_at       = Column("created_date", DateTime(timezone=True), server_default=func.now())
    updated_at       = Column("modified_date", DateTime(timezone=True), onupdate=func.now())

    is_active        = Column(Boolean, default=True)
    ooo_until        = Column(DateTime, nullable=True)
    delegate_id      = Column(Integer, ForeignKey("user_details.user_id"), nullable=True)

    @property
    def name(self):
        if self.firstName and self.lastName:
            return f"{self.firstName} {self.lastName}"
        return self.firstName or self.lastName or "Unknown"

    delegate               = relationship("User", remote_side=[id], foreign_keys=[delegate_id])
    super_admin            = relationship("User", remote_side=[id], foreign_keys=[super_admin_id])
    submitted_requests     = relationship("WorkflowRequest", foreign_keys="WorkflowRequest.submitter_id", back_populates="submitter")
    approval_actions       = relationship("ApprovalAction", foreign_keys="ApprovalAction.approver_id", back_populates="approver")


class ApproverGroup(Base):
    __tablename__ = "approver_groups"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), nullable=False)
    description = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    members     = relationship("ApproverGroupMember", back_populates="group")
    stages      = relationship("WorkflowStage", back_populates="approver_group")


class ApproverGroupMember(Base):
    __tablename__ = "approver_group_members"

    id       = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("approver_groups.id", ondelete="CASCADE"))
    user_id  = Column(Integer, ForeignKey("user_details.user_id", ondelete="CASCADE"))
    sequential_order = Column(Integer, default=0)
    # Notified like every other member, but their decision never counts
    # toward stage completion and never blocks the stage (see
    # routers/approvals.py:_check_stage_completion).
    is_optional = Column(Boolean, default=False)

    group    = relationship("ApproverGroup", back_populates="members")
    user     = relationship("User")


class Workflow(Base):
    __tablename__ = "workflows"

    id                   = Column(Integer, primary_key=True, index=True)
    name                 = Column(String(200), nullable=False)
    description          = Column(Text)
    type                 = Column(SAEnum(WorkflowType), nullable=False)
    folder_trigger       = Column(String(300))
    is_active            = Column(Boolean, default=True)

    escalation_hours        = Column(Integer, default=24)
    rejection_behavior      = Column(SAEnum(RejectionBehavior), default=RejectionBehavior.stop)
    notification_channel    = Column(SAEnum(NotificationChannel), default=NotificationChannel.email)
    auto_approve_hours      = Column(Integer, nullable=True)
    amount_threshold        = Column(Float, nullable=True)

    # Where to send the browser after a one-click email action resolves.
    # Falls back to FRONTEND_URL/requests/{id} when not set.
    success_redirect_url = Column(String(500), nullable=True)
    failure_redirect_url = Column(String(500), nullable=True)
    auto_approve_conditions = Column(JSON, nullable=True)

    # ── Reminder settings ────────────────────────────────────────────────────
    # Hours after a stage starts before the first reminder is sent.
    # e.g. 8 → first reminder fires 8 hours after stage start.
    reminder_after_hours    = Column(Integer, nullable=True)
    # How often (hours) to repeat the reminder until the stage is resolved.
    # e.g. 4 → re-send every 4 hours after the first reminder.
    reminder_interval_hours = Column(Integer, nullable=True)

    # Derived/templated values available to message templates (stage
    # `instructions`, and ad-hoc messages via send_message). Each entry is
    # {"name": str, "formula": str}, e.g. {"name": "tax", "formula": "amount * 0.18"}.
    # Evaluated in order via template_utils.resolve_template_variables, on
    # top of the request's own fields and request_metadata. See
    # template_utils.py for the formula grammar (arithmetic only, no calls).
    message_variables    = Column(JSON, nullable=True)

    # Stable marker used by code that needs to recognise "this is the Vendor
    # Onboarding workflow" (e.g. to fire VO.4 on submit, or to skip the
    # generic completion email once email_templates.py sends the real one)
    # instead of matching on workflow.name, which an admin can rename freely.
    email_process_key   = Column(String(50), nullable=True)

    created_by_id        = Column(Integer, ForeignKey("user_details.user_id"))
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), onupdate=func.now())

    created_by  = relationship("User")
    stages      = relationship("WorkflowStage", back_populates="workflow", order_by="WorkflowStage.order", cascade="all, delete-orphan")
    requests    = relationship("WorkflowRequest", back_populates="workflow")

    @property
    def total_stages(self):
        return len(self.stages)


class WorkflowStage(Base):
    __tablename__ = "workflow_stages"

    id                = Column(Integer, primary_key=True, index=True)
    workflow_id       = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"))
    name              = Column(String(200), nullable=False)
    type              = Column(SAEnum(WorkflowType), nullable=False)
    order             = Column("stage_order", Integer, nullable=False)
    approver_group_id = Column(Integer, ForeignKey("approver_groups.id"))
    sla_hours         = Column(Integer, default=48)
    voting_rule       = Column(SAEnum(VotingRule), default=VotingRule.any)

    # Free-text overrides for the email action buttons. When null, the
    # stage `type` preset (see NotificationService.STAGE_TYPE_LABELS) is used.
    approve_label     = Column(String(50), nullable=True)
    reject_label      = Column(String(50), nullable=True)
    is_optional       = Column(Boolean, default=False)
    instructions      = Column(Text, nullable=True)

    # Parallel stage execution: stages sharing the same non-null parallel_group
    # integer (within the same workflow) start simultaneously and all must
    # complete before the workflow advances to the next serial stage.
    # NULL means the stage runs serially in order.
    parallel_group    = Column(Integer, nullable=True)

    condition_field   = Column(String(100))
    condition_op      = Column(String(20))
    condition_value   = Column(String(300))

    workflow          = relationship("Workflow", back_populates="stages")
    approver_group    = relationship("ApproverGroup", back_populates="stages")
    request_stages    = relationship("RequestStage", back_populates="stage")


class WorkflowRequest(Base):
    __tablename__ = "workflow_requests"

    id            = Column(Integer, primary_key=True, index=True)
    title         = Column(String(300), nullable=False)
    description   = Column(Text)
    document_name = Column(String(300))
    document_url  = Column(String(500))
    document_type = Column(String(100))
    folder_path   = Column(String(300))
    amount        = Column(Float, nullable=True)
    department    = Column(String(100))
    request_type  = Column(String(100))
    request_metadata = Column(JSON, nullable=True)

    workflow_id   = Column(Integer, ForeignKey("workflows.id"))
    submitter_id  = Column(Integer, ForeignKey("user_details.user_id"))
    status        = Column(SAEnum(RequestStatus), default=RequestStatus.pending)
    current_stage = Column(Integer, default=0)

    submitted_at  = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at   = Column(DateTime(timezone=True), nullable=True)
    sla_deadline  = Column(DateTime(timezone=True), nullable=True)

    # Frozen copy of the workflow's stage/group config at the moment this
    # request was submitted (see routers/requests.py:_build_workflow_snapshot).
    # The live Workflow/WorkflowStage/ApproverGroup rows can keep changing —
    # this column is what lets a request's "as submitted" config be shown
    # later even after an admin edits or reorders the workflow.
    workflow_snapshot = Column(JSON, nullable=True)

    workflow      = relationship("Workflow", back_populates="requests")
    submitter     = relationship("User", foreign_keys=[submitter_id], back_populates="submitted_requests")
    stages        = relationship("RequestStage", back_populates="request", order_by="RequestStage.stage_order", cascade="all, delete-orphan")
    activity_log  = relationship("ActivityLog", back_populates="request", order_by="ActivityLog.created_at.desc()")

    @property
    def workflow_name(self):
        return self.workflow.name if self.workflow else None

    @property
    def submitter_name(self):
        return self.submitter.name if self.submitter else "System"

    @property
    def total_stages(self):
        return self.workflow.total_stages if self.workflow else 0

    @property
    def pending_group_name(self):
        if self.status != RequestStatus.pending:
            return None
        if not self.workflow:
            return "Unknown Group"
        for s in self.workflow.stages:
            if s.order == self.current_stage:
                return s.approver_group.name if s.approver_group else "Unknown Group"
        return "Unknown Group"

    @property
    def history(self):
        return [
            {
                "id": log.id,
                "action": log.action,
                "detail": log.detail,
                "created_at": log.created_at,
                "user_name": log.user.name if log.user else "System"
            }
            for log in self.activity_log
        ]


class RequestStage(Base):
    __tablename__ = "request_stages"

    id              = Column(Integer, primary_key=True, index=True)
    request_id      = Column(Integer, ForeignKey("workflow_requests.id", ondelete="CASCADE"))
    stage_id        = Column(Integer, ForeignKey("workflow_stages.id"))
    stage_order     = Column(Integer, nullable=False)
    status          = Column(SAEnum(RequestStatus), default=RequestStatus.pending)
    started_at      = Column(DateTime(timezone=True), nullable=True)
    completed_at    = Column(DateTime(timezone=True), nullable=True)
    sla_deadline    = Column(DateTime(timezone=True), nullable=True)
    is_sla_breached = Column(Boolean, default=False)

    # Tracks when the last reminder was sent for this stage instance.
    # Used by the scheduler to enforce reminder_after_hours + reminder_interval_hours.
    last_reminded_at = Column(DateTime(timezone=True), nullable=True)

    request         = relationship("WorkflowRequest", back_populates="stages")
    stage           = relationship("WorkflowStage", back_populates="request_stages")
    actions         = relationship("ApprovalAction", back_populates="request_stage", cascade="all, delete-orphan")

    @property
    def stage_name(self):
        return self.stage.name if self.stage else None

    @property
    def stage_type(self):
        return self.stage.type.value if self.stage and self.stage.type else None

    @property
    def group_name(self):
        if self.stage and self.stage.approver_group:
            return self.stage.approver_group.name
        return None

    @property
    def voting_rule(self):
        return self.stage.voting_rule.value if self.stage and self.stage.voting_rule else None

    @property
    def approve_label(self):
        return self.stage.approve_label if self.stage else None

    @property
    def reject_label(self):
        return self.stage.reject_label if self.stage else None

    @property
    def is_optional(self):
        return self.stage.is_optional if self.stage else False

    @property
    def parallel_group(self):
        return self.stage.parallel_group if self.stage else None


class ApprovalAction(Base):
    __tablename__ = "approval_actions"

    id               = Column(Integer, primary_key=True, index=True)
    request_stage_id = Column(Integer, ForeignKey("request_stages.id", ondelete="CASCADE"))
    approver_id      = Column(Integer, ForeignKey("user_details.user_id"))
    decision         = Column(SAEnum(ApprovalDecision), nullable=False)
    comment          = Column(Text)
    # Optional document uploaded alongside the approve/reject decision
    # (e.g. a signed copy, a rejection memo). Set via /api/requests/upload
    # then referenced here by the client.
    document_name    = Column(String(300), nullable=True)
    document_url     = Column(String(500), nullable=True)
    delegated_to_id  = Column(Integer, ForeignKey("user_details.user_id"), nullable=True)
    acted_at         = Column(DateTime(timezone=True), server_default=func.now())

    request_stage    = relationship("RequestStage", back_populates="actions")
    approver         = relationship("User", foreign_keys=[approver_id], back_populates="approval_actions")
    delegated_to     = relationship("User", foreign_keys=[delegated_to_id])

    @property
    def approver_name(self):
        return self.approver.name if self.approver else None


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id          = Column(Integer, primary_key=True, index=True)
    request_id  = Column(Integer, ForeignKey("workflow_requests.id", ondelete="CASCADE"))
    user_id     = Column(Integer, ForeignKey("user_details.user_id"), nullable=True)
    action      = Column(String(100), nullable=False)
    detail      = Column(Text)
    stage_order = Column(Integer, nullable=True)
    extra       = Column(JSON, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    request     = relationship("WorkflowRequest", back_populates="activity_log")
    user        = relationship("User")


class WebhookConfig(Base):
    __tablename__ = "webhook_configs"

    id          = Column(Integer, primary_key=True, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=True)
    event       = Column(String(100), nullable=False)
    url         = Column(String(500), nullable=False)
    secret      = Column(String(200))
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class ScheduledMessage(Base):
    """
    A recurring ad-hoc message created via POST /requests/{id}/send-message
    when the caller supplies reminder_interval_hours. The scheduler
    (services/escalation.py:send_message_reminders) resends `message` every
    reminder_interval_hours for as long as the parent request's status
    hasn't changed away from pending (i.e. "the relevant status was not
    achieved") and reminders_sent < max_reminders (if set).
    """
    __tablename__ = "scheduled_messages"

    id                      = Column(Integer, primary_key=True, index=True)
    request_id              = Column(Integer, ForeignKey("workflow_requests.id", ondelete="CASCADE"))
    sender_id               = Column(Integer, ForeignKey("user_details.user_id"), nullable=True)
    to                       = Column(String(50), nullable=False)   # "submitter" | "current_approvers" | "custom"
    custom_emails           = Column(JSON, nullable=True)
    subject                 = Column(String(300), nullable=True)
    message                  = Column(Text, nullable=False)
    reminder_interval_hours = Column(Integer, nullable=False)
    max_reminders            = Column(Integer, nullable=True)        # None = unlimited until resolved
    reminders_sent           = Column(Integer, default=0)
    last_sent_at             = Column(DateTime(timezone=True), nullable=True)
    is_active                = Column(Boolean, default=True)
    created_at               = Column(DateTime(timezone=True), server_default=func.now())

    request = relationship("WorkflowRequest")
    sender  = relationship("User")


class StandaloneMessage(Base):
    """
    A standalone notification fired without any workflow request context.
    Created via POST /api/messages/ — supports the "send message on button
    click" use case (Messaging #4) where a user wants to fire a notification
    to one or more email addresses without creating or referencing a request.
    Supports {{field}} template rendering against a flat key-value `context`
    dict and optional recurring resend (reminder_interval_hours).
    """
    __tablename__ = "standalone_messages"

    id                      = Column(Integer, primary_key=True, index=True)
    sender_id               = Column(Integer, ForeignKey("user_details.user_id"), nullable=True)
    to_emails               = Column(JSON, nullable=False)   # list of email strings
    subject                 = Column(String(300), nullable=True)
    message                 = Column(Text, nullable=False)
    context                 = Column(JSON, nullable=True)    # flat key-value dict for {{}} rendering
    reminder_interval_hours = Column(Integer, nullable=True) # None = one-shot
    max_reminders           = Column(Integer, nullable=True) # None = unlimited until manually stopped
    reminders_sent          = Column(Integer, default=0)
    last_sent_at            = Column(DateTime(timezone=True), nullable=True)
    is_active               = Column(Boolean, default=True)
    created_at              = Column(DateTime(timezone=True), server_default=func.now())

    sender = relationship("User")


class EmailFooter(Base):
    """
    Shared footer library for email_templates — most templates point at one
    of these by id rather than carrying their own wording, so a single edit
    here updates every mail using it. A template may still override with its
    own footer_override_reason/footer_override_legal when it genuinely needs
    to say something different from every other mail using the same footer.
    """
    __tablename__ = "email_footers"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(200), nullable=False)
    reason_text  = Column(Text, nullable=False)
    legal_line   = Column(Text, nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())


class EmailTemplate(Base):
    """
    One admin-editable transactional email, identified by a stable mail_key
    (e.g. "VO.2") that trigger call sites reference — never the row id, so
    re-seeding or reordering never breaks a trigger. Rendered by
    services/email_templates.py:render_email_template, which walks these
    columns in the same order NotificationEmailTemplates' mockup does
    (masthead -> status strip -> heading -> intro -> detail table -> CTA ->
    outro -> footer), substituting {{merge_tag}} placeholders via
    template_utils.render_template.
    """
    __tablename__ = "email_templates"

    id                      = Column(Integer, primary_key=True, index=True)
    process_key             = Column(String(50), nullable=False)   # e.g. "vendor_onboarding"
    mail_key                = Column(String(20), nullable=False, unique=True)  # e.g. "VO.2"
    mail_label              = Column(String(200), nullable=False)
    enabled                 = Column(Boolean, default=True)

    from_address            = Column(String(300), nullable=True)
    reply_to                = Column(String(300), nullable=True)

    status_strip_text       = Column(String(300), nullable=True)
    status_strip_tone       = Column(String(10), nullable=True)   # info | ok | warn | bad

    subject                 = Column(String(300), nullable=False)
    preheader               = Column(String(300), nullable=True)
    heading                 = Column(String(300), nullable=False)
    intro                   = Column(Text, nullable=True)
    detail_rows             = Column(JSON, nullable=True)   # [[label, value_template], ...]
    cta_label                = Column(String(100), nullable=True)
    cta_url                  = Column(String(500), nullable=True)
    outro                    = Column(Text, nullable=True)

    footer_id                = Column(Integer, ForeignKey("email_footers.id"), nullable=True)
    footer_override_reason   = Column(Text, nullable=True)
    footer_override_legal    = Column(Text, nullable=True)

    sample_data              = Column(JSON, nullable=True)   # used by the preview/test-send endpoints

    updated_by_id             = Column(BigInteger, ForeignKey("user_details.user_id"), nullable=True)
    created_at                = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                = Column(DateTime(timezone=True), onupdate=func.now())

    footer      = relationship("EmailFooter")
    updated_by  = relationship("User")


# --- BUDGET MODELS ---

"""
models.py — SQLAlchemy ORM models.
"""
# pyrefly: ignore [missing-import]
from sqlalchemy import (
    Column, String, Integer, SmallInteger, Date, DateTime, Text,
    ForeignKey, UniqueConstraint, CheckConstraint, Index
)
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime



# ── Organisation ──────────────────────────────────────────────────────────────
class Organisation(Base):
    __tablename__ = "organisation"

    org_code      = Column(String(20),  primary_key=True)
    name          = Column(String(120), nullable=False)
    base_currency = Column(String(3),   nullable=False, default="INR")
    fiscal_year   = Column(String(20),  nullable=False)

    departments = relationship("Department", back_populates="organisation")


# ── Department ────────────────────────────────────────────────────────────────
class Department(Base):
    __tablename__ = "department"
    __table_args__ = (
        UniqueConstraint("name", name="uq_department_name"),
        UniqueConstraint("wbs",  name="uq_department_wbs"),
    )

    dept_code         = Column(String(20),  primary_key=True)
    name              = Column(String(120), nullable=False)
    org_code          = Column(String(20),  ForeignKey("organisation.org_code"), nullable=False)
    wbs               = Column(String(20),  nullable=False)
    head_employee_code = Column(String(20), ForeignKey("employee.employee_code"), nullable=True)

    organisation = relationship("Organisation", back_populates="departments")
    projects     = relationship("Project",      back_populates="department")
    employees    = relationship("Employee",     back_populates="department",
                                foreign_keys="Employee.dept_code")
    head         = relationship("Employee",     foreign_keys=[head_employee_code])


# ── Project ───────────────────────────────────────────────────────────────────
class Project(Base):
    __tablename__ = "project"
    __table_args__ = (
        UniqueConstraint("wbs", name="uq_project_wbs"),
    )

    project_code = Column(String(20),  primary_key=True)
    name         = Column(String(120), nullable=False)
    dept_code    = Column(String(20),  ForeignKey("department.dept_code"), nullable=False)
    wbs          = Column(String(20),  nullable=False)

    department = relationship("Department", back_populates="projects")
    activities = relationship("Activity",   back_populates="project")


# ── CostType ──────────────────────────────────────────────────────────────────
class CostType(Base):
    __tablename__ = "cost_type"
    __table_args__ = (
        UniqueConstraint("tag", name="uq_cost_type_tag"),
    )

    cost_type_code = Column(String(20),  primary_key=True)
    name           = Column(String(120), nullable=False)
    tag            = Column(String(20),  nullable=False)

    activities     = relationship("Activity",    back_populates="cost_type")
    sub_activities = relationship("SubActivity", back_populates="cost_type")


# ── Status ────────────────────────────────────────────────────────────────────
class Status(Base):
    __tablename__ = "status"
    __table_args__ = (
        UniqueConstraint("name", name="uq_status_name"),
    )

    status_code = Column(String(20), primary_key=True)
    name        = Column(String(60), nullable=False)
    sort_order  = Column(Integer,    nullable=False, default=0)

    activities     = relationship("Activity",    back_populates="status")
    sub_activities = relationship("SubActivity", back_populates="status")


# ── Employee ──────────────────────────────────────────────────────────────────
class Employee(Base):
    __tablename__ = "employee"
    __table_args__ = (
        UniqueConstraint("name", name="uq_employee_name"),
    )

    employee_code = Column(String(20),  primary_key=True)
    name          = Column(String(120), nullable=False)
    title         = Column(String(120))
    email         = Column(String(120), nullable=True)
    user_id       = Column("user_id", Integer, ForeignKey("user_details.user_id"), nullable=True)
    dept_code     = Column(String(20),  ForeignKey("department.dept_code"))
    manager_code  = Column(String(20),  ForeignKey("employee.employee_code"))

    department     = relationship("Department", back_populates="employees",
                                  foreign_keys=[dept_code])
    manager        = relationship("Employee",   remote_side="Employee.employee_code",
                                  foreign_keys=[manager_code], overlaps="direct_reports")
    direct_reports = relationship("Employee",   foreign_keys=[manager_code],
                                  overlaps="manager")
    activities     = relationship("Activity",    back_populates="employee")
    sub_activities = relationship("SubActivity", back_populates="employee")


# ── Activity ──────────────────────────────────────────────────────────────────
class Activity(Base):
    __tablename__ = "activity"
    __table_args__ = (
        UniqueConstraint("wbs", name="uq_activity_wbs"),
        CheckConstraint("is_leaf IN (0,1)", name="ck_activity_leaf"),
    )

    activity_code  = Column(String(20),  primary_key=True)
    name           = Column(String(120), nullable=False)
    project_code   = Column(String(20),  ForeignKey("project.project_code"),     nullable=False)
    cost_type_code = Column(String(20),  ForeignKey("cost_type.cost_type_code"), nullable=True)
    employee_code  = Column(String(20),  ForeignKey("employee.employee_code"),   nullable=True)
    status_code    = Column(String(20),  ForeignKey("status.status_code"),       nullable=True)
    wbs            = Column(String(20),  nullable=False)
    is_leaf        = Column(SmallInteger, nullable=False, default=1)

    # annual totals (kept in sync with phase rows)
    allocated = Column(Integer, nullable=False, default=0)
    pr        = Column(Integer, nullable=False, default=0)
    po        = Column(Integer, nullable=False, default=0)
    invoiced  = Column(Integer, nullable=False, default=0)

    # board-approved total (separate from phased allocation)
    approved  = Column(Integer, nullable=False, default=0)

    project        = relationship("Project",   back_populates="activities")
    cost_type      = relationship("CostType",  back_populates="activities")
    employee       = relationship("Employee",  back_populates="activities")
    status         = relationship("Status",    back_populates="activities")
    sub_activities = relationship("SubActivity", back_populates="parent_activity",
                                  cascade="all, delete-orphan")
    phases         = relationship("ActivityPhase", back_populates="activity",
                                  cascade="all, delete-orphan",
                                  order_by="ActivityPhase.period_no")


# ── ActivityPhase — monthly phasing for an activity ───────────────────────────
class ActivityPhase(Base):
    __tablename__ = "activity_phase"
    __table_args__ = (
        UniqueConstraint("activity_code", "period_no", name="uq_activity_phase"),
    )

    id            = Column(Integer,    primary_key=True, autoincrement=True)
    activity_code = Column(String(20), ForeignKey("activity.activity_code", ondelete="CASCADE"), nullable=False)
    period_no     = Column(Integer,    nullable=False)   # 1–12
    alloc         = Column(Integer,    nullable=False, default=0)
    pr            = Column(Integer,    nullable=False, default=0)
    po            = Column(Integer,    nullable=False, default=0)
    cons          = Column(Integer,    nullable=False, default=0)  # consumed / actuals

    activity = relationship("Activity", back_populates="phases")


# ── SubActivity ───────────────────────────────────────────────────────────────
class SubActivity(Base):
    __tablename__ = "sub_activity"
    __table_args__ = (
        UniqueConstraint("wbs", name="uq_sub_activity_wbs"),
        CheckConstraint("is_leaf IN (0,1)", name="ck_sub_activity_leaf"),
        CheckConstraint("level BETWEEN 1 AND 3", name="ck_sub_activity_lvl"),
    )

    subactivity_code     = Column(String(20),  primary_key=True)
    name                 = Column(String(120), nullable=False)
    parent_activity_code = Column(String(20),  ForeignKey("activity.activity_code"),   nullable=False)
    level                = Column(Integer,     nullable=False)
    cost_type_code       = Column(String(20),  ForeignKey("cost_type.cost_type_code"), nullable=True)
    employee_code        = Column(String(20),  ForeignKey("employee.employee_code"),   nullable=True)
    status_code          = Column(String(20),  ForeignKey("status.status_code"),       nullable=True)
    wbs                  = Column(String(20),  nullable=False)
    is_leaf              = Column(SmallInteger, nullable=False, default=1)

    allocated = Column(Integer, nullable=False, default=0)
    pr        = Column(Integer, nullable=False, default=0)
    po        = Column(Integer, nullable=False, default=0)
    invoiced  = Column(Integer, nullable=False, default=0)
    approved  = Column(Integer, nullable=False, default=0)

    parent_activity = relationship("Activity",  back_populates="sub_activities")
    cost_type       = relationship("CostType",  back_populates="sub_activities")
    employee        = relationship("Employee",  back_populates="sub_activities")
    status          = relationship("Status",    back_populates="sub_activities")
    phases          = relationship("SubActivityPhase", back_populates="sub_activity",
                                   cascade="all, delete-orphan",
                                   order_by="SubActivityPhase.period_no")


# ── SubActivityPhase ──────────────────────────────────────────────────────────
class SubActivityPhase(Base):
    __tablename__ = "sub_activity_phase"
    __table_args__ = (
        UniqueConstraint("subactivity_code", "period_no", name="uq_sub_activity_phase"),
    )

    id               = Column(Integer,    primary_key=True, autoincrement=True)
    subactivity_code = Column(String(20), ForeignKey("sub_activity.subactivity_code", ondelete="CASCADE"), nullable=False)
    period_no        = Column(Integer,    nullable=False)
    alloc            = Column(Integer,    nullable=False, default=0)
    pr               = Column(Integer,    nullable=False, default=0)
    po               = Column(Integer,    nullable=False, default=0)
    cons             = Column(Integer,    nullable=False, default=0)

    sub_activity = relationship("SubActivity", back_populates="phases")


# ── BudgetVersion ─────────────────────────────────────────────────────────────
class BudgetVersion(Base):
    __tablename__ = "budget_version"
    __table_args__ = (
        CheckConstraint("is_current IN (0,1)", name="ck_version_current"),
        CheckConstraint("is_locked  IN (0,1)", name="ck_version_locked"),
    )

    version_code = Column(String(20),  primary_key=True)
    name         = Column(String(120), nullable=False)
    fiscal_year  = Column(String(20),  nullable=True)
    created_date = Column(Date,        nullable=False)
    basis        = Column(String(200))
    is_current   = Column(SmallInteger, nullable=False, default=0)
    is_locked    = Column(SmallInteger, nullable=False, default=0)


# ── FiscalPeriod ──────────────────────────────────────────────────────────────
class FiscalPeriod(Base):
    __tablename__ = "fiscal_period"
    __table_args__ = (
        UniqueConstraint("period_code", name="uq_fiscal_period_code"),
        CheckConstraint("quarter IN ('Q1','Q2','Q3','Q4')", name="ck_fiscal_quarter"),
        CheckConstraint("state IN ('Open','Current','Closed')", name="ck_fiscal_state"),
    )

    period_no   = Column(Integer,    primary_key=True)
    period_code = Column(String(10), nullable=False)
    quarter     = Column(String(2),  nullable=False)
    month       = Column(String(20), nullable=False)
    state       = Column(String(10), nullable=False)
    fiscal_year = Column(String(20), nullable=False)


# ── Transfer ──────────────────────────────────────────────────────────────────
class Transfer(Base):
    __tablename__ = "transfer"

    id            = Column(String(20),  primary_key=True)
    transfer_type = Column(String(20),  nullable=False)
    from_code     = Column(String(20),  nullable=False)
    to_code       = Column(String(20),  nullable=False)
    amount        = Column(Integer,     nullable=False)
    employee_code = Column(String(20),  ForeignKey("employee.employee_code"), nullable=True)
    note          = Column(String(300))
    transfer_date = Column(Date,        nullable=False)
    status        = Column(String(20),  nullable=False, default="Approved")


# ── ChangeRequest ─────────────────────────────────────────────────────────────
# Persists the governed change-request workflow (months / carry / pull / transfer)
# raised from the Budget Book / Dashboard / Line Drawer. Distinct from Transfer
# (which is immediate, legacy, annual-total-only). A ChangeRequest stays
# "Pending" until Finance approves or rejects it; approval is what actually
# moves money — see /api/change-requests/{id}/decide in main.py.
class ChangeRequest(Base):
    __tablename__ = "change_request"
    __table_args__ = (
        CheckConstraint("request_type IN ('months','carry','pull','transfer')", name="ck_cr_type"),
        CheckConstraint("status IN ('Pending','Approved','Rejected')", name="ck_cr_status"),
    )

    id              = Column(String(20),  primary_key=True)
    request_type    = Column(String(20),  nullable=False)
    a_code          = Column(String(20),  nullable=False)   # source activity_code
    b_code          = Column(String(20),  nullable=True)    # target activity_code (transfer only)
    period_from     = Column(Integer,     nullable=False)   # 1-12
    period_to       = Column(Integer,     nullable=False)   # 1-12 (== period_from for transfer)
    amount          = Column(Integer,     nullable=False)
    reason          = Column(String(500))
    requested_by    = Column(String(20),  ForeignKey("employee.employee_code"), nullable=True)
    decided_by      = Column(String(20),  ForeignKey("employee.employee_code"), nullable=True)
    status          = Column(String(20),  nullable=False, default="Pending")
    created_at      = Column(DateTime,    nullable=False, default=datetime.utcnow)
    decided_at      = Column(DateTime,    nullable=True)


# ── BudgetUpload ──────────────────────────────────────────────────────────────
# A department's Excel upload is STAGED, not applied immediately. It sits as
# "Pending" until that department's head approves or rejects it. Approval marks
# the department complete for that fiscal-year cycle; rows are merged into
# Activity only once every department has approved.
class BudgetUpload(Base):
    __tablename__ = "budget_upload"
    __table_args__ = (
        CheckConstraint("status IN ('Pending','Approved','Rejected')", name="ck_upload_status"),
    )

    id            = Column(String(20),  primary_key=True)
    filename      = Column(String(255), nullable=False)
    fiscal_year   = Column(String(20),  nullable=True)
    dept_code     = Column(String(20),  ForeignKey("department.dept_code"), nullable=True)
    status        = Column(String(20),  nullable=False, default="Pending")
    staged_rows   = Column(Text,        nullable=True)   # JSON — validated rows awaiting approval
    rows_inserted = Column(Integer,     nullable=False, default=0)  # preview until approved, actual after
    rows_updated  = Column(Integer,     nullable=False, default=0)
    rows_skipped  = Column(Integer,     nullable=False, default=0)
    requested_by  = Column(String(20),  ForeignKey("employee.employee_code"), nullable=True)
    decided_by    = Column(String(20),  ForeignKey("employee.employee_code"), nullable=True)
    uploaded_at   = Column(DateTime,    nullable=False, default=datetime.utcnow)
    decided_at    = Column(DateTime,    nullable=True)
    merged_at     = Column(DateTime,    nullable=True)
    notes         = Column(Text)

    department    = relationship("Department", foreign_keys=[dept_code])

class VendorMaster(Base):
    __tablename__ = "vendor_master"
    
    vendor_id = Column(Integer, primary_key=True, index=True)
    bp_no = Column(String(255), unique=True)
    name = Column(String(255))
    gst_number = Column(String(255))
    pan = Column(String(255))
    company_code = Column(String(255))
    email = Column(String(255), nullable=True)          # vendor login email
    city_name = Column(String(255), nullable=True)      # city
    sap_created_on = Column(Date)
    sap_changed_on = Column(Date)
    sys_created_date = Column(DateTime)
    sys_modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    addresses = relationship("VendorAddress", back_populates="vendor")

class VendorAddress(Base):
    __tablename__ = "vendor_address"

    vendor_address_id = Column(Integer, primary_key=True, index=True)
    address_id = Column(String(255), unique=True, index=True)
    address_type = Column(String(255))
    city_name = Column(String(255))
    country = Column(String(255))
    created_date = Column(DateTime, default=datetime.utcnow)
    is_default = Column(Boolean, default=False)
    modified_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    postal_code = Column(String(255))
    street_and_house_number = Column(String(255))
    street_name_1 = Column(String(255))
    vendor_id = Column(Integer, ForeignKey("vendor_master.vendor_id"), index=True)

    vendor = relationship("VendorMaster", back_populates="addresses")

# pyrefly: ignore [missing-import]
from sqlalchemy import BigInteger, Numeric

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(BigInteger, primary_key=True, index=True)
    po_number = Column(String(50), index=True)
    vendor_id = Column(BigInteger)
    vendor_name = Column(String(255))
    company_code = Column(String(20))
    company_name = Column(String(200))
    po_type = Column(String(50))
    currency = Column(String(20))
    total_value = Column(Numeric(15, 2))
    net_value = Column(Numeric(15, 2))
    status = Column(String(50))
    created_date = Column(DateTime)

class PurchaseRequisition(Base):
    __tablename__ = "purchase_requisitions"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pr_number = Column(String(50), unique=True, nullable=False)
    location_id = Column(BigInteger, nullable=False)
    requested_by = Column(BigInteger, nullable=False)
    required_date = Column(Date)
    remarks = Column(Text)
    status = Column(String(50)) # CREATED, RELEASED, PARTIALLY_RELEASED
    total_amount = Column(Numeric(15, 2))
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

class PurchaseRequisitionItem(Base):
    __tablename__ = "purchase_requisition_items"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    purchase_requisition_id = Column(BigInteger, nullable=False)
    material_id = Column(BigInteger, nullable=False)
    sku = Column(String(255))
    quantity = Column(Numeric(19, 2))
    uom = Column(String(50))
    estimated_price = Column(Numeric(15, 2))
    total_price = Column(Numeric(15, 2))
    status = Column(String(50)) # CREATED, RELEASED

class PurchaseRequisitionItemVendor(Base):
    __tablename__ = "purchase_requisition_item_vendors"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    purchase_requisition_item_id = Column(BigInteger, nullable=False)
    vendor_id = Column(BigInteger, nullable=False)
    status = Column(String(50)) # SENT, ACCEPTED, REJECTED
    sent_at = Column(DateTime)
    bp_no = Column(String(255), nullable=True)

class VendorQuotation(Base):
    __tablename__ = "vendor_quotations"
    quotation_id = Column(BigInteger, primary_key=True, autoincrement=True)
    pr_id = Column(BigInteger, nullable=False)
    vendor_id = Column(BigInteger, nullable=False)
    quotation_number = Column(String(255), nullable=False, unique=True)
    quotation_date = Column(Date)
    status = Column(String(255))
    currency = Column(String(255))
    subtotal_amount = Column(Numeric(38, 2))
    gst_total_amount = Column(Numeric(38, 2))
    freight_amount = Column(Numeric(38, 2))
    grand_total_amount = Column(Numeric(38, 2))
    valid_until = Column(Date)
    created_date = Column(DateTime)
    modified_date = Column(DateTime)
    bp_no = Column(String(50))
    
class VendorQuotationItem(Base):
    __tablename__ = "vendor_quotation_items"
    quotation_item_id = Column(BigInteger, primary_key=True, autoincrement=True)
    quotation_id = Column(BigInteger, nullable=False)
    pr_line_id = Column(BigInteger, nullable=False)
    item_code = Column(String(255))
    description = Column(Text)
    pr_qty = Column(Numeric(38, 2))
    quoted_qty = Column(Numeric(38, 2))
    uom = Column(String(255))
    unit_price = Column(Numeric(38, 2))
    gst_percent = Column(Numeric(38, 2))
    gst_amount = Column(Numeric(38, 2))
    freight_amount = Column(Numeric(38, 2))
    line_total = Column(Numeric(38, 2))
    delivery_date = Column(Date)
    
class VendorQuotationDocument(Base):
    __tablename__ = "vendor_quotation_documents"
    document_id = Column(Integer, primary_key=True, autoincrement=True)
    vendor_quotation_id = Column(BigInteger)
    file_path = Column(String(500))
    file_name = Column(String(255))

class PortalPurchaseOrder(Base):
    __tablename__ = "portal_purchase_orders"
    id = Column(BigInteger, primary_key=True, index=True)
    pr_id = Column(BigInteger)
    quotation_id = Column(BigInteger)
    po_number = Column(String(50), index=True)
    po_date = Column(DateTime)
    vendor_id = Column(BigInteger)
    vendor_name = Column(String(255))
    company_code = Column(String(20))
    company_name = Column(String(200))
    po_type = Column(String(50))
    currency = Column(String(20))
    grand_total = Column(Numeric(15, 2))
    status = Column(String(50))
    requested_delivery_date = Column(Date)
    confirmed_delivery_date = Column(Date)

class PortalPurchaseOrderItem(Base):
    __tablename__ = "portal_purchase_order_items"
    id = Column(BigInteger, primary_key=True, index=True)
    po_id = Column(BigInteger, ForeignKey("portal_purchase_orders.id"))
    line_number = Column(Integer)
    material_number = Column(String(100))
    material_description = Column(String(500))
    quantity = Column(Numeric(15, 2))
    uom = Column(String(20))
    unit_price = Column(Numeric(15, 2))
    net_value = Column(Numeric(15, 2))
    tax_percent = Column(Numeric(10, 2))
    tax_amount = Column(Numeric(15, 2))
    total_value = Column(Numeric(15, 2))


class PortalASN(Base):
    __tablename__ = "portal_asns"
    id = Column(BigInteger, primary_key=True, index=True)
    asn_number = Column(String(50), unique=True, index=True) # ASN-YYYY-XXXX
    po_id = Column(String(100), index=True) # Using String to handle poNumber or poId flexibly
    vendor_bpno = Column(String(100))
    invoice_number = Column(String(100))
    irn = Column(String(100))
    eway_bill = Column(String(100))
    ewb_valid_to = Column(String(100))
    vehicle_number = Column(String(100))
    transporter_code = Column(String(100))
    lr_number = Column(String(100))
    dispatch_date = Column(String(100))
    expected_delivery = Column(String(100))
    packaging = Column(String(100))
    
    # Files
    tax_invoice_file = Column(String(255))
    eway_bill_file = Column(String(255))
    packing_list_file = Column(String(255))
    pdir_file = Column(String(255))
    deviation_file = Column(String(255))
    
    status = Column(String(50), default="IN_TRANSIT")
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("PortalASNItem", back_populates="asn")


class PortalASNItem(Base):
    __tablename__ = "portal_asn_items"
    id = Column(BigInteger, primary_key=True, index=True)
    asn_id = Column(BigInteger, ForeignKey("portal_asns.id"))
    line_number = Column(Integer)
    part_number = Column(String(100))
    quantity_shipped = Column(Numeric(15, 2))
    batch_heat_number = Column(String(100), nullable=True)

    asn = relationship("PortalASN", back_populates="items")

