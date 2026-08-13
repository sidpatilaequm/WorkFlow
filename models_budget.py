"""
models.py — SQLAlchemy ORM models.
"""

from sqlalchemy import (
    Column, String, Integer, SmallInteger, Date, DateTime, Text,
    ForeignKey, UniqueConstraint, CheckConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship
from datetime import datetime


class Base(DeclarativeBase):
    pass


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
    password      = Column(String(120), nullable=True)
    admin_email   = Column(String(120), nullable=True)
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
