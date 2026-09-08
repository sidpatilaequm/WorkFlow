from sqlalchemy import create_engine, MetaData
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Check your .env file.")

engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base(metadata=MetaData(schema="multimedia_governance"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- BUDGET DB STUFF ---

"""
database.py — SQLAlchemy connection layer.

Falls back to a local SQLite file (budget_app.db) automatically when no
MySQL credentials are configured, so the app works out-of-the-box with
zero server setup.

Priority:
  1. DATABASE_URL env var (full override)
  2. DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD from .env → MySQL
  3. SQLite fallback (budget_app.db in the project root)

Env file selection:
  Set APP_ENV=localtest to load .env.localtest (SQLite, no MySQL needed).
  Set APP_ENV=serverdb to load .env.serverdb (production DB via SSH tunnel — see
  .env.serverdb.example and ../connect-server-db.sh).
  Any other APP_ENV=<name> loads .env.<name>; unset loads plain .env.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_base_dir = Path(__file__).parent
_app_env  = os.getenv("APP_ENV", "")

_env_file = _base_dir / (f".env.{_app_env}" if _app_env else ".env")

load_dotenv(dotenv_path=_env_file, override=True)
print(f"[database] Loaded env from: {_env_file}")


def _build_database_url() -> tuple[str, bool]:
    if url := os.getenv("DATABASE_URL"):
        return url, url.startswith("sqlite")

    host     = os.getenv("DB_HOST",     "").strip()
    port     = os.getenv("DB_PORT",     "3306").strip()
    name     = os.getenv("DB_NAME",     "budget_app").strip()
    user     = os.getenv("DB_USER",     "").strip()
    password = os.getenv("DB_PASSWORD", "").strip()

    if host and user and password:
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset=utf8mb4"
        return url, False

    db_path = _base_dir / "budget_app.db"
    print(f"[database] No MySQL credentials — using SQLite: {db_path}")
    return f"sqlite:///{db_path}", True


DATABASE_URL, _IS_SQLITE = _build_database_url()

_engine_kwargs: dict = {"echo": False}
if _IS_SQLITE:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_recycle"]  = 3600

engine       = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from models import Base
    Base.metadata.create_all(bind=engine)
    _migrate_schema()
    _seed_reference_data()


def _migrate_schema():
    """Lightweight, idempotent column additions for existing DB files.

    create_all() only creates missing tables, it never alters existing
    ones — so a DB file created before a model change needs its new
    columns added by hand here.
    """
    from sqlalchemy import text, inspect

    inspector = inspect(engine)
    table_names = inspector.get_table_names()

    if "budget_upload" in table_names:
        existing_cols = {c["name"] for c in inspector.get_columns("budget_upload")}
        additions = {
            "dept_code":     "VARCHAR(20)",
            "status":        "VARCHAR(20) NOT NULL DEFAULT 'Approved'",  # pre-existing uploads were applied immediately
            "staged_rows":   "TEXT",
            "requested_by":  "VARCHAR(20)",
            "decided_by":    "VARCHAR(20)",
            "decided_at":    "DATETIME",
            "merged_at":     "DATETIME",
        }
        with engine.begin() as conn:
            for col, ddl_type in additions.items():
                if col not in existing_cols:
                    conn.execute(text(f"ALTER TABLE budget_upload ADD COLUMN {col} {ddl_type}"))

    if "department" in table_names:
        existing_cols = {c["name"] for c in inspector.get_columns("department")}
        if "head_employee_code" not in existing_cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE department ADD COLUMN head_employee_code VARCHAR(20)"))

    if "employee" in table_names:
        existing_cols = {c["name"] for c in inspector.get_columns("employee")}
        additions = {
            "email":       "VARCHAR(120)",
            "password":    "VARCHAR(120)",
            "admin_email": "VARCHAR(120)",
        }
        with engine.begin() as conn:
            for col, ddl_type in additions.items():
                if col not in existing_cols:
                    conn.execute(text(f"ALTER TABLE employee ADD COLUMN {col} {ddl_type}"))


# ── monthly phase helper ──────────────────────────────────────────────────────
def _make_phases(alloc_list, pr_list, po_list, cons_list):
    """Return list of dicts with period_no 1-12."""
    return [
        {"period_no": i+1, "alloc": int(alloc_list[i]), "pr": int(pr_list[i]),
         "po": int(po_list[i]), "cons": int(cons_list[i])}
        for i in range(12)
    ]


# ── Seed data ─────────────────────────────────────────────────────────────────
def _seed_reference_data():
    # NOTE: Department/Project/Employee/Activity/SubActivity used to be seeded here too,
    # as one big interlinked demo fixture keyed off a fake Organisation("ORG-001"). That
    # table is gone — departments are created entirely through the UI now (optionally
    # assigned to real companies via department_company) and never auto-seeded, so the
    # demo chain was removed rather than patched. Only the reference data with no FK to
    # department survives here.
    from models import CostType, Status, BudgetVersion, FiscalPeriod

    db = SessionLocal()
    try:
        if db.query(CostType).count():
            return

        # ── Cost types ──
        db.add(CostType(cost_type_code="OPEX",  name="Operating Expenditure", tag="Opex"))
        db.add(CostType(cost_type_code="CAPEX", name="Capital Expenditure",   tag="Capex"))
        db.flush()

        # ── Statuses ──
        for code, name, order in [
            ("STS-IP",  "In Progress", 1), ("STS-NS",  "Not Started", 2),
            ("STS-DONE","Completed",   3), ("STS-HOLD","On Hold",      4),
        ]:
            db.add(Status(status_code=code, name=name, sort_order=order))
        db.flush()

        # ── Budget version ──
        from datetime import date as ddate
        db.add(BudgetVersion(version_code="BV-001", name="Annual Budget FY 2026-27",
                             fiscal_year="FY 2026-27", created_date=ddate(2026, 4, 1),
                             basis="Excel upload", is_current=1, is_locked=0))

        # ── Fiscal periods ──
        for row in [
            (1,"Apr-26","Q1","April","Current"),(2,"May-26","Q1","May","Open"),
            (3,"Jun-26","Q1","June","Open"),(4,"Jul-26","Q2","July","Open"),
            (5,"Aug-26","Q2","August","Open"),(6,"Sep-26","Q2","September","Open"),
            (7,"Oct-26","Q3","October","Open"),(8,"Nov-26","Q3","November","Open"),
            (9,"Dec-26","Q3","December","Open"),(10,"Jan-27","Q4","January","Open"),
            (11,"Feb-27","Q4","February","Open"),(12,"Mar-27","Q4","March","Open"),
        ]:
            db.add(FiscalPeriod(period_no=row[0], period_code=row[1], quarter=row[2],
                                month=row[3], state=row[4], fiscal_year="FY 2026-27"))

        db.commit()
        print("[database] Seed data inserted.")

    except Exception as e:
        db.rollback()
        print(f"[database] Seed skipped or failed: {e}")
    finally:
        db.close()
