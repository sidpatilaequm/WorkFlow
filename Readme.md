# WorkflowOS — Document Approval Workflow Engine

Full-stack: **FastAPI + MySQL** backend · **React + Vite** frontend.

---

## Features

| Module | What's built |
|---|---|
| **Auth** | JWT login/register, role-based access (submitter / approver / admin) |
| **Workflows** | CRUD, 4 types (approval/review/acknowledgement/signature), folder triggers |
| **Stages** | Per-stage SLA, voting rules (any/all/sequential), conditional branching |
| **Requests** | Submit, cancel, track with live stage-progress dots |
| **Approvals** | Approve / reject / delegate, rejection behavior (stop/restart/escalate) |
| **Analytics** | Approval rate, avg resolution time, SLA breach count, activity feed |
| **Webhooks** | HMAC-signed payloads, Slack Block Kit builder, per-workflow or global |

---

## Project Structure

```
workflow-engine/
├── backend/
│   ├── main.py            # FastAPI app, CORS, router registration
│   ├── database.py        # SQLAlchemy engine + session
│   ├── models.py          # All ORM models (8 tables)
│   ├── schemas.py         # Pydantic request/response schemas
│   ├── auth_utils.py      # JWT + bcrypt + role guard
│   ├── webhook_utils.py   # Webhook dispatcher + Slack Block Kit
│   ├── requirements.txt
│   ├── alembic.ini
│   └── routers/
│       ├── auth.py        # POST /api/auth/login|register
│       ├── workflows.py   # CRUD /api/workflows
│       ├── requests.py    # Submit/list /api/requests
│       ├── stages.py      # Add/delete /api/stages
│       ├── approvals.py   # Approve/reject /api/approvals
│       └── analytics.py   # Summary /api/analytics/summary
└── frontend/
    ├── index.html
    ├── vite.config.js
    ├── package.json
    └── src/
        ├── App.jsx            # Root + Nav + routing
        ├── main.jsx
        ├── styles/global.css  # Full dark enterprise theme
        ├── hooks/useAuth.jsx  # AuthContext
        ├── services/api.js    # All API calls
        └── pages/
            ├── Login.jsx
            ├── Dashboard.jsx
            ├── Workflows.jsx  # Workflow builder UI
            ├── Requests.jsx   # Submit + approval queue
            └── Analytics.jsx  # Metrics + activity feed
```

---

## Database Schema (MySQL)

```
users                   → id, name, email, hashed_password, role, department, ooo_until, delegate_id
approver_groups         → id, name, description
approver_group_members  → group_id, user_id
workflows               → id, name, type, folder_trigger, escalation_hours, rejection_behavior, ...
workflow_stages         → id, workflow_id, order, type, approver_group_id, sla_hours, voting_rule, condition_*
workflow_requests       → id, title, document_name, amount, department, workflow_id, submitter_id, status, current_stage
request_stages          → id, request_id, stage_id, status, started_at, sla_deadline, is_sla_breached
approval_actions        → id, request_stage_id, approver_id, decision, comment, delegated_to_id
activity_log            → id, request_id, user_id, action, detail, created_at
webhook_configs         → id, workflow_id, event, url, secret, is_active
```

---

## Setup

### 1. MySQL

```sql
CREATE DATABASE workflow_engine CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'wfuser'@'localhost' IDENTIFIED BY 'yourpassword';
GRANT ALL PRIVILEGES ON workflow_engine.* TO 'wfuser'@'localhost';
```

Update `DATABASE_URL` in `backend/database.py` or set env var:
```
DATABASE_URL=mysql+pymysql://wfuser:yourpassword@localhost:3306/workflow_engine
```

### 2. Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Tables are auto-created on first run via `Base.metadata.create_all()`.

API docs: http://localhost:8000/docs

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

App: http://localhost:3000

### 4. First user

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Admin","email":"admin@co.com","password":"admin123","role":"admin"}'
```

Then create an approver group via the API before building workflows (the UI currently uses group ID 1):

```bash
# First get your token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@co.com","password":"admin123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create approver group
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Finance Team","email":"finance@co.com","password":"pass123","role":"approver","department":"Finance"}'
```

---

## API Reference

### Auth
| Method | Path | Body |
|---|---|---|
| POST | /api/auth/register | `{name, email, password, role, department}` |
| POST | /api/auth/login | `{email, password}` → `{access_token, user}` |

### Workflows
| Method | Path | Notes |
|---|---|---|
| GET | /api/workflows/ | List all |
| POST | /api/workflows/ | Admin only. Includes `stages[]` |
| PATCH | /api/workflows/{id} | Partial update |
| DELETE | /api/workflows/{id} | Admin only |

### Requests
| Method | Path | Notes |
|---|---|---|
| GET | /api/requests/ | Filter: `?status=pending&workflow_id=1` |
| POST | /api/requests/ | Submit a new request |
| DELETE | /api/requests/{id} | Cancel (own requests only) |

### Approvals
| Method | Path | Body |
|---|---|---|
| POST | /api/approvals/ | `{request_id, decision, comment, delegated_to_id?}` |
| GET | /api/approvals/pending | Returns request IDs awaiting current user |

### Analytics
| Method | Path |
|---|---|
| GET | /api/analytics/summary |

---

## Webhook Payload Example

```json
{
  "event": "stage.approved",
  "timestamp": 1718000000,
  "request": {
    "id": 42,
    "title": "Vendor Invoice - Infosys Q2",
    "status": "pending",
    "current_stage": 1,
    "workflow_id": 3,
    "submitted_at": "2026-06-11T08:00:00",
    "document_name": "Invoice_Q2_Infosys.pdf",
    "amount": 250000.00,
    "department": "Finance"
  }
}
```

Signed with `X-Signature-256: sha256=<hmac>` when a secret is configured.

---

## What's Remaining / Next Steps

### High priority
- [ ] **Approver group management UI** — currently groups must be created via API
- [ ] **SLA breach background job** — `celery beat` task to mark `is_sla_breached` and fire escalation webhooks
- [ ] **Email notifications** — integrate `fastapi-mail` triggered from `fire_webhook()`
- [ ] **Delegation UI** — OOO toggle + delegate selector on user profile page
- [ ] **Conditional branching UI** — stage-level condition editor (field / operator / value)

### Nice to have
- [ ] Alembic migration files for schema versioning
- [ ] Document upload (S3/MinIO integration)
- [ ] Bulk approve from approval queue
- [ ] Request detail drawer with full audit trail
- [ ] Role-based nav hiding (submitters see only Requests + Dashboard)
- [ ] Approver group editor in Workflows page

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI 0.111, SQLAlchemy 2.0, Alembic, PyMySQL |
| Auth | python-jose (JWT), passlib (bcrypt) |
| Frontend | React 18, Vite 5 |
| Styling | Pure CSS custom properties (no component library) |
| DB | MySQL 8+ |