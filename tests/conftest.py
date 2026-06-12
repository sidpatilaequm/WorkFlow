"""
conftest.py — shared fixtures for all test modules.

Uses an in-memory SQLite DB so no real MySQL is needed.
Run with:  pytest tests/ -v
"""
import sys
import os

# ── MUST happen before any backend module is imported ────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force-set env vars (override anything already loaded from .env)
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key-for-pytest-only"
os.environ["ALGORITHM"] = "HS256"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "60"
os.environ["REFRESH_TOKEN_EXPIRE_DAYS"] = "7"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest
from fastapi.testclient import TestClient

# ── Build the test engine first, then patch database module before main runs ──
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Patch database module so main.py's create_all uses our SQLite engine
import database
database.engine = engine
database.SessionLocal = TestingSessionLocal

# Strip MySQL schema — SQLite has no concept of schemas
from database import Base
Base.metadata.schema = None
for table in Base.metadata.tables.values():
    table.schema = None

# Now it's safe to import the app (main.py's create_all will use SQLite)
from database import get_db
from main import app
import models
from auth_utils import hash_password


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all tables once per test session."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    """Fresh DB session per test, rolled back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db):
    """TestClient with DB dependency overridden to use the test session."""
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helper factories ──────────────────────────────────────────────────────────

def make_user(db, email="user@example.com", role=models.UserRole.submitter,
              first="Test", last="User", is_active=True, password="password123"):
    u = models.User(
        email=email,
        password=hash_password(password),
        firstName=first,
        lastName=last,
        role=role,
        is_active=is_active,
    )
    db.add(u)
    db.flush()
    return u


def make_group(db, name="Finance Approvers", members=None):
    g = models.ApproverGroup(name=name, description="Test group")
    db.add(g)
    db.flush()
    for idx, user in enumerate(members or []):
        db.add(models.ApproverGroupMember(
            group_id=g.id, user_id=user.id, sequential_order=idx
        ))
    db.flush()
    return g


def make_workflow(db, created_by, group, name="Test Workflow",
                  wf_type=models.WorkflowType.approval,
                  voting_rule=models.VotingRule.any,
                  rejection_behavior=models.RejectionBehavior.stop,
                  amount_threshold=None):
    wf = models.Workflow(
        name=name,
        type=wf_type,
        escalation_hours=24,
        rejection_behavior=rejection_behavior,
        notification_channel=models.NotificationChannel.email,
        created_by_id=created_by.id,
        amount_threshold=amount_threshold,
    )
    db.add(wf)
    db.flush()
    stage = models.WorkflowStage(
        workflow_id=wf.id,
        name="Stage 1",
        type=wf_type,
        order=1,
        approver_group_id=group.id,
        sla_hours=48,
        voting_rule=voting_rule,
    )
    db.add(stage)
    db.flush()
    return wf


def make_request(db, submitter, workflow, title="Invoice #001", amount=None):
    from datetime import datetime, timedelta
    req = models.WorkflowRequest(
        title=title,
        workflow_id=workflow.id,
        submitter_id=submitter.id,
        amount=amount,
        status=models.RequestStatus.pending,
        current_stage=1,
    )
    db.add(req)
    db.flush()
    # create RequestStage for stage order 1
    stage_def = workflow.stages[0]
    now = datetime.utcnow()
    rs = models.RequestStage(
        request_id=req.id,
        stage_id=stage_def.id,
        stage_order=1,
        status=models.RequestStatus.pending,
        started_at=now,
        sla_deadline=now + timedelta(hours=48),
    )
    db.add(rs)
    db.flush()
    return req
