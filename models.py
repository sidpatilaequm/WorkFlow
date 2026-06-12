from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean,
    Float, ForeignKey, Enum as SAEnum, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base
import enum


# ─── Enums ────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    submitter = "submitter"
    approver  = "approver"
    admin     = "admin"

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

    id               = Column("userId", Integer, primary_key=True, index=True)
    super_admin_id   = Column("super_admin_id", Integer, ForeignKey("user_details.userId"), nullable=True)
    email            = Column(String(150), unique=True, nullable=False, index=True)
    password         = Column("password", String(255), nullable=False)
    firstName        = Column("firstName", String(100))
    lastName         = Column("lastName", String(100))
    phoneNumber      = Column("phoneNumber", String(50))
    signupDate       = Column("signupDate", String(50))
    designation      = Column("designation", String(100))
    onboardingStatus = Column("onboardingStatus", String(100))
    onboardingToken  = Column("onboardingToken", String(255))
    tokenExpiry      = Column("tokenExpiry", DateTime, nullable=True)
    role             = Column("user_type", SAEnum(UserRole), default=UserRole.submitter)
    company_id       = Column("company_id", Integer, nullable=True)
    created_at       = Column("created_date", DateTime(timezone=True), server_default=func.now())
    updated_at       = Column("modified_date", DateTime(timezone=True), onupdate=func.now())

    is_active        = Column(Boolean, default=True)
    ooo_until        = Column(DateTime, nullable=True)
    delegate_id      = Column(Integer, ForeignKey("user_details.userId"), nullable=True)

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

    id               = Column(Integer, primary_key=True, index=True)
    group_id         = Column(Integer, ForeignKey("approver_groups.id", ondelete="CASCADE"))
    user_id          = Column(Integer, ForeignKey("user_details.userId", ondelete="CASCADE"))
    sequential_order = Column(Integer, default=0)

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
    auto_approve_conditions = Column(JSON, nullable=True)

    # ── Reminder settings ────────────────────────────────────────────────────
    # Hours after a stage starts before the first reminder is sent.
    # e.g. 8 → first reminder fires 8 hours after stage start.
    reminder_after_hours    = Column(Integer, nullable=True)
    # How often (hours) to repeat the reminder until the stage is resolved.
    # e.g. 4 → re-send every 4 hours after the first reminder.
    reminder_interval_hours = Column(Integer, nullable=True)

    created_by_id        = Column(Integer, ForeignKey("user_details.userId"))
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
    order             = Column(Integer, nullable=False)
    approver_group_id = Column(Integer, ForeignKey("approver_groups.id"))
    sla_hours         = Column(Integer, default=48)
    voting_rule       = Column(SAEnum(VotingRule), default=VotingRule.any)
    is_optional       = Column(Boolean, default=False)
    instructions      = Column(Text, nullable=True)

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
    submitter_id  = Column(Integer, ForeignKey("user_details.userId"))
    status        = Column(SAEnum(RequestStatus), default=RequestStatus.pending)
    current_stage = Column(Integer, default=0)

    submitted_at  = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at   = Column(DateTime(timezone=True), nullable=True)
    sla_deadline  = Column(DateTime(timezone=True), nullable=True)

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


class ApprovalAction(Base):
    __tablename__ = "approval_actions"

    id               = Column(Integer, primary_key=True, index=True)
    request_stage_id = Column(Integer, ForeignKey("request_stages.id", ondelete="CASCADE"))
    approver_id      = Column(Integer, ForeignKey("user_details.userId"))
    decision         = Column(SAEnum(ApprovalDecision), nullable=False)
    comment          = Column(Text)
    delegated_to_id  = Column(Integer, ForeignKey("user_details.userId"), nullable=True)
    acted_at         = Column(DateTime(timezone=True), server_default=func.now())

    request_stage    = relationship("RequestStage", back_populates="actions")
    approver         = relationship("User", foreign_keys=[approver_id], back_populates="approval_actions")
    delegated_to     = relationship("User", foreign_keys=[delegated_to_id])


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id          = Column(Integer, primary_key=True, index=True)
    request_id  = Column(Integer, ForeignKey("workflow_requests.id", ondelete="CASCADE"))
    user_id     = Column(Integer, ForeignKey("user_details.userId"), nullable=True)
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
