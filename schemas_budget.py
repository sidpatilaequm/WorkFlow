from datetime import date, datetime
from typing import Optional, List, Any
# pyrefly: ignore [missing-import]
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

class EmployeeOut(BaseModel):
    employee_code: str
    name: str
    title: Optional[str]
    dept_code: Optional[str]
    manager_code: Optional[str]

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
