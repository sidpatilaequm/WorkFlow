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
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_base_dir = Path(__file__).parent
_app_env  = os.getenv("APP_ENV", "")

if _app_env == "localtest":
    _env_file = _base_dir / ".env.localtest"
else:
    _env_file = _base_dir / ".env"

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
    from models import (
        Organisation, Department, Project, CostType, Status,
        Employee, Activity, ActivityPhase, SubActivity, SubActivityPhase,
        BudgetVersion, FiscalPeriod
    )

    db = SessionLocal()
    try:
        if db.query(Organisation).count():
            return

        # ── Organisation ──
        org = Organisation(org_code="ORG-001", name="Aequm India",
                           base_currency="INR", fiscal_year="FY 2026-27")
        db.add(org); db.flush()

        # ── Departments ──
        for code, name, wbs in [
            ("ENG", "Engineering",       "1.1"),
            ("SMK", "Sales & Marketing", "1.2"),
            ("OPS", "Operations",        "1.3"),
            ("FIN", "Finance & Admin",   "1.4"),
            ("CSU", "Customer Success",  "1.5"),
        ]:
            db.add(Department(dept_code=code, name=name, org_code="ORG-001", wbs=wbs))
        db.flush()

        # ── Projects ──
        for code, name, dept, wbs in [
            ("PRJ-ENG-01", "Platform R&D",           "ENG", "1.1.1"),
            ("PRJ-ENG-02", "Mobile App",              "ENG", "1.1.2"),
            ("PRJ-SEC-ENG","Cybersecurity Uplift",    "ENG", "1.1.3"),
            ("PRJ-SMK-01", "Brand Campaign 2026",     "SMK", "1.2.1"),
            ("PRJ-SMK-02", "CRM Rollout",             "SMK", "1.2.2"),
            ("PRJ-OPS-01", "Supply Chain Automation", "OPS", "1.3.1"),
            ("PRJ-OPS-02", "Facilities",              "OPS", "1.3.2"),
            ("PRJ-SEC-OPS","Cybersecurity Uplift",    "OPS", "1.3.3"),
            ("PRJ-FIN-01", "ERP Implementation",      "FIN", "1.4.1"),
            ("PRJ-FIN-02", "Compliance",              "FIN", "1.4.2"),
            ("PRJ-SEC-FIN","Cybersecurity Uplift",    "FIN", "1.4.3"),
            ("PRJ-CSU-01", "Onboarding Program",      "CSU", "1.5.1"),
        ]:
            db.add(Project(project_code=code, name=name, dept_code=dept, wbs=wbs))
        db.flush()

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

        # ── Employees ──
        for code, name, title, dept, mgr in [
            ("EMP-01", "Asha Pillai",   "Chief Financial Officer",            None,  None),
            ("EMP-02", "Priya Nair",    "Head of Engineering",                "ENG", "EMP-01"),
            ("EMP-03", "Rahul Verma",   "Engineering Lead",                   "ENG", "EMP-02"),
            ("EMP-04", "Sana Kapoor",   "Design Lead",                        "ENG", "EMP-02"),
            ("EMP-05", "Arjun Mehta",   "Head of Sales & Marketing",          "SMK", "EMP-01"),
            ("EMP-06", "Neha Singh",    "Marketing Manager",                  "SMK", "EMP-05"),
            ("EMP-07", "Vikram Rao",    "Head of Operations",                 "OPS", "EMP-01"),
            ("EMP-08", "Meera Iyer",    "Facilities Manager",                 "OPS", "EMP-07"),
            ("EMP-09", "Sunil Das",     "Head of Finance & Admin",            "FIN", "EMP-01"),
            ("EMP-10", "Ravi Krishnan", "Chief Information Security Officer", "ENG", "EMP-01"),
            ("EMP-11", "Divya Menon",   "Head of Customer Success",           "CSU", "EMP-01"),
        ]:
            db.add(Employee(employee_code=code, name=name, title=title,
                            dept_code=dept, manager_code=mgr))
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

        # ── Activities with monthly phases ──
        # Each tuple: (code, name, proj, cost_type, emp, status, wbs, approved,
        #              alloc[12], pr[12], po[12], cons[12])
        # Monthly figures taken directly from Budget_App_v4.html seed data
        acts = [
          ("ACT-001","Backend development","PRJ-ENG-01","OPEX","EMP-02","STS-IP","1.1.1.1",2500000,
           [208000]*11+[212000],[197600]*11+[201400],[176800]*11+[180200],
           [212000,472000,413000,295000,0,0,0,0,0,0,0,0]),

          ("ACT-002","QA & automation","PRJ-ENG-01","OPEX","EMP-03","STS-IP","1.1.1.2",900000,
           [75000]*12,[71250]*12,[63750]*12,
           [75000,164000,144000,102000,0,0,0,0,0,0,0,0]),

          ("ACT-003","Cloud infrastructure","PRJ-ENG-01","CAPEX","EMP-02","STS-DONE","1.1.1.3",1600000,
           [133000]*11+[137000],[133000]*11+[137000],[133000]*11+[137000],
           [137000,640000,560000,400000,0,0,0,0,0,0,0,0]),

          ("ACT-004","UI/UX design","PRJ-ENG-02","OPEX","EMP-04","STS-IP","1.1.2.1",600000,
           [50000]*12,[47500]*12,[42500]*12,
           [50000,100000,88000,62000,0,0,0,0,0,0,0,0]),

          ("ACT-005","Mobile development","PRJ-ENG-02","OPEX","EMP-03","STS-IP","1.1.2.2",1400000,
           [117000]*11+[113000],[111150]*11+[107350],[99450]*11+[96050],
           [113000,120000,105000,75000,0,0,0,0,0,0,0,0]),

          ("ACT-SEC-ENG","Security tooling (Eng)","PRJ-SEC-ENG","CAPEX","EMP-10","STS-IP","1.1.3.1",1200000,
           [100000]*12,[95000]*12,[85000]*12,
           [120000,120000,120000,0,0,0,0,0,0,0,0,0]),

          ("ACT-006","Digital advertising","PRJ-SMK-01","OPEX","EMP-05","STS-IP","1.2.1.1",1200000,
           [100000]*12,[95000]*12,[85000]*12,
           [100000,312000,273000,195000,0,0,0,0,0,0,0,0]),

          ("ACT-007","Events & expos","PRJ-SMK-01","OPEX","EMP-05","STS-NS","1.2.1.2",800000,
           [67000]*11+[63000],[63650]*11+[59850],[56950]*11+[53550],
           [63000,0,0,0,0,0,0,0,0,0,0,0]),

          ("ACT-008","Software licenses","PRJ-SMK-02","CAPEX","EMP-06","STS-DONE","1.2.2.1",500000,
           [42000]*11+[38000],[42000]*11+[38000],[42000]*11+[38000],
           [38000,200000,175000,125000,0,0,0,0,0,0,0,0]),

          ("ACT-009","Sales enablement","PRJ-SMK-02","OPEX","EMP-06","STS-IP","1.2.2.2",350000,
           [29000]*11+[31000],[27550]*11+[29450],[24650]*11+[26350],
           [31000,48000,42000,30000,0,0,0,0,0,0,0,0]),

          ("ACT-010","Process consulting","PRJ-OPS-01","OPEX","EMP-07","STS-IP","1.3.1.1",700000,
           [58000]*11+[62000],[55100]*11+[58900],[49300]*11+[52700],
           [62000,160000,140000,100000,0,0,0,0,0,0,0,0]),

          ("ACT-011","Workflow tooling","PRJ-OPS-01","CAPEX","EMP-07","STS-IP","1.3.1.2",950000,
           [79000]*11+[81000],[75050]*11+[76950],[67150]*11+[68850],
           [81000,240000,210000,150000,0,0,0,0,0,0,0,0]),

          ("ACT-012","Office expansion","PRJ-OPS-02","CAPEX","EMP-08","STS-IP","1.3.2.1",2000000,
           [167000]*11+[163000],[158650]*11+[154850],[146125]*11+[142625],
           [163000,700000,612000,438000,0,0,0,0,0,0,0,0]),

          ("ACT-013","Utilities & maintenance","PRJ-OPS-02","OPEX","EMP-08","STS-IP","1.3.2.2",450000,
           [38000]*11+[32000],[36100]*11+[30400],[32300]*11+[27200],
           [32000,92000,80000,58000,0,0,0,0,0,0,0,0]),

          ("ACT-SEC-OPS","Security tooling (Ops)","PRJ-SEC-OPS","CAPEX","EMP-10","STS-IP","1.3.3.1",1050000,
           [87500]*12,[83125]*12,[74375]*12,
           [105000,105000,105000,0,0,0,0,0,0,0,0,0]),

          ("ACT-014","ERP licenses","PRJ-FIN-01","CAPEX","EMP-09","STS-DONE","1.4.1.1",1800000,
           [150000]*12,[150000]*12,[150000]*12,
           [150000,720000,630000,450000,0,0,0,0,0,0,0,0]),

          ("ACT-015","Implementation services","PRJ-FIN-01","OPEX","EMP-09","STS-IP","1.4.1.2",1100000,
           [92000]*11+[88000],[87400]*11+[83600],[78200]*11+[74800],
           [88000,208000,182000,130000,0,0,0,0,0,0,0,0]),

          ("ACT-016","Audit & filings","PRJ-FIN-02","OPEX","EMP-09","STS-IP","1.4.2.1",400000,
           [33000]*11+[37000],[31350]*11+[35150],[28050]*11+[31450],
           [37000,72000,63000,45000,0,0,0,0,0,0,0,0]),

          ("ACT-SEC-FIN","Security tooling (Fin)","PRJ-SEC-FIN","CAPEX","EMP-10","STS-IP","1.4.3.1",750000,
           [62500]*12,[59375]*12,[53125]*12,
           [75000,75000,75000,0,0,0,0,0,0,0,0,0]),

          ("ACT-017","Training content","PRJ-CSU-01","OPEX","EMP-11","STS-IP","1.5.1.1",300000,
           [25000]*12,[23750]*12,[21250]*12,
           [25000,56000,49000,35000,0,0,0,0,0,0,0,0]),

          ("ACT-018","Support tooling","PRJ-CSU-01","CAPEX","EMP-11","STS-IP","1.5.1.2",450000,
           [38000]*11+[32000],[36100]*11+[30400],[32300]*11+[27200],
           [32000,80000,70000,50000,0,0,0,0,0,0,0,0]),
        ]

        for row in acts:
            code,name,proj,ct,emp,sts,wbs,approved = row[:8]
            alloc_m,pr_m,po_m,cons_m = row[8],row[9],row[10],row[11]
            act = Activity(
                activity_code=code, name=name, project_code=proj,
                cost_type_code=ct, employee_code=emp, status_code=sts,
                wbs=wbs, is_leaf=1, approved=approved,
                allocated=sum(alloc_m), pr=sum(pr_m),
                po=sum(po_m), invoiced=sum(cons_m),
            )
            db.add(act)
            db.flush()
            for i in range(12):
                db.add(ActivityPhase(
                    activity_code=code, period_no=i+1,
                    alloc=int(alloc_m[i]), pr=int(pr_m[i]),
                    po=int(po_m[i]), cons=int(cons_m[i]),
                ))

        db.flush()

        # ── Sub-activities with phases ──
        subs = [
          ("SACT-005-1-1","iOS - UI layer","ACT-005",1,"OPEX","EMP-03","STS-IP","1.1.2.2.1.1",462000,
           [38610]*11+[37290],[36680]*11+[35426],[32819]*11+[31697],
           [37290,39600,34650,24750,0,0,0,0,0,0,0,0]),

          ("SACT-005-1-2","iOS - Networking","ACT-005",1,"OPEX","EMP-03","STS-IP","1.1.2.2.1.2",308000,
           [25740]*11+[24860],[24453]*11+[23617],[21879]*11+[21131],
           [24860,26400,23100,16500,0,0,0,0,0,0,0,0]),

          ("SACT-005-2","Android app","ACT-005",1,"OPEX","EMP-03","STS-IP","1.1.2.2.2",630000,
           [52650]*11+[50850],[50017]*11+[48307],[44752]*11+[43222],
           [50850,54000,47250,33750,0,0,0,0,0,0,0,0]),

          ("SACT-006-1","Search ads","ACT-006",1,"OPEX","EMP-05","STS-IP","1.2.1.1.1",660000,
           [55000]*12,[52250]*12,[46750]*12,
           [55000,171600,150150,107250,0,0,0,0,0,0,0,0]),

          ("SACT-006-2","Social ads","ACT-006",1,"OPEX","EMP-05","STS-IP","1.2.1.1.2",540000,
           [45000]*12,[42750]*12,[38250]*12,
           [45000,140400,122850,87750,0,0,0,0,0,0,0,0]),

          ("SACT-012-1","Foundation","ACT-012",1,"CAPEX","EMP-08","STS-IP","1.3.2.1.1.1",330000,
           [27555]*11+[26895],[26177]*11+[25550],[24110]*11+[23533],
           [26895,115500,100980,72270,0,0,0,0,0,0,0,0]),

          ("SACT-012-2","Framing","ACT-012",1,"CAPEX","EMP-08","STS-IP","1.3.2.1.1.2",270000,
           [22545]*11+[22005],[21418]*11+[20905],[19727]*11+[19254],
           [22005,94500,82620,59130,0,0,0,0,0,0,0,0]),

          ("SACT-012-3","Interiors","ACT-012",2,"CAPEX","EMP-08","STS-IP","1.3.2.1.1.3",400000,
           [33400]*11+[32600],[31730]*11+[30970],[29225]*11+[28525],
           [32600,140000,122400,87600,0,0,0,0,0,0,0,0]),

          ("SACT-012-4","Furniture and fixtures","ACT-012",1,"CAPEX","EMP-08","STS-IP","1.3.2.1.2",600000,
           [50100]*11+[48900],[47595]*11+[46455],[43838]*11+[42788],
           [48900,210000,183600,131400,0,0,0,0,0,0,0,0]),

          ("SACT-012-5","IT cabling","ACT-012",1,"CAPEX","EMP-08","STS-IP","1.3.2.1.3",400000,
           [33400]*11+[32600],[31730]*11+[30970],[29225]*11+[28525],
           [32600,140000,122400,87600,0,0,0,0,0,0,0,0]),
        ]

        for row in subs:
            code,name,parent,level,ct,emp,sts,wbs,approved = row[:9]
            alloc_m,pr_m,po_m,cons_m = row[9],row[10],row[11],row[12]
            sa = SubActivity(
                subactivity_code=code, name=name, parent_activity_code=parent,
                level=level, cost_type_code=ct, employee_code=emp,
                status_code=sts, wbs=wbs, is_leaf=1, approved=approved,
                allocated=sum(alloc_m), pr=sum(pr_m),
                po=sum(po_m), invoiced=sum(cons_m),
            )
            db.add(sa)
            db.flush()
            for i in range(12):
                db.add(SubActivityPhase(
                    subactivity_code=code, period_no=i+1,
                    alloc=int(alloc_m[i]), pr=int(pr_m[i]),
                    po=int(po_m[i]), cons=int(cons_m[i]),
                ))

        db.commit()
        print("[database] Seed data inserted.")

    except Exception as e:
        db.rollback()
        print(f"[database] Seed skipped or failed: {e}")
    finally:
        db.close()
