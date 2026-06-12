# WorkflowOS — Document Approval Workflow Engine

Full-stack: **FastAPI + MySQL** backend · **React + Vite** frontend.

---

## Table of Contents

1. [Features](#features)
2. [Project Structure](#project-structure)
3. [Database Schema](#database-schema)
4. [Setup & Installation](#setup--installation)
5. [Environment Variables](#environment-variables)
6. [API Reference](#api-reference)
   - [Auth](#auth)
   - [Workflows](#workflows)
   - [Stages & Approver Groups](#stages--approver-groups)
   - [Requests](#requests)
   - [Approvals](#approvals)
   - [Analytics](#analytics)
7. [Authentication](#authentication)
8. [Role-Based Access](#role-based-access)
9. [Workflow Engine Logic](#workflow-engine-logic)
10. [Notifications & Webhooks](#notifications--webhooks)
11. [Background Jobs (Scheduler)](#background-jobs-scheduler)
12. [Error Responses](#error-responses)
13. [Tech Stack](#tech-stack)
14. [Roadmap](#roadmap)

---

## Features

| Module | What's built |
|---|---|
| **Auth** | JWT login/register, access + refresh tokens, role-based access (submitter / approver / admin) |
| **Workflows** | CRUD, 4 stage types (approval/review/acknowledgement/signature), folder triggers, amount-based auto-approve |
| **Stages** | Per-stage SLA deadlines, voting rules (any/all/sequential), per-stage conditional branching |
| **Requests** | Submit with document upload, cancel, track with live stage progress, one-click email approval |
| **Approvals** | Approve / reject / delegate, rejection behaviors (stop/restart/escalate), duplicate prevention |
| **Analytics** | Approval rate, avg resolution time, SLA breach count, by-workflow breakdown, approver performance, activity feed |
| **Notifications** | Async SMTP email with approve/reject buttons, Slack Block Kit DMs |
| **Webhooks** | HMAC-signed outgoing payloads, Slack interactive button handler |
| **Scheduler** | APScheduler jobs: SLA escalation every 15 min, pending-approver reminders every hour |

---

## Project Structure

```
Vendors_Workflow/
└── backend/
    ├── main.py                  # FastAPI app, lifespan, CORS, routers, global error handler
    ├── database.py              # SQLAlchemy engine + session factory
    ├── models.py                # All ORM models (10 tables)
    ├── schemas.py               # Pydantic request/response schemas
    ├── auth_utils.py            # JWT (access + refresh + approval tokens), bcrypt, role guards
    ├── webhook_utils.py         # Outgoing webhook dispatcher + Slack Block Kit builder
    ├── requirements.txt
    ├── alembic.ini
    ├── .env                     # Environment config (never commit secrets)
    ├── uploads/                 # Uploaded document files (served at /api/uploads/)
    ├── routers/
    │   ├── auth.py              # POST /api/auth/register|login|refresh  GET /api/auth/me
    │   ├── workflows.py         # CRUD /api/workflows
    │   ├── requests.py          # Submit/list/cancel/one-click  /api/requests
    │   ├── stages.py            # Approver groups + stage management  /api/stages
    │   ├── approvals.py         # Approve/reject/delegate  /api/approvals
    │   └── analytics.py        # Metrics  /api/analytics
    └── services/
        ├── notification.py      # Async email (aiosmtplib) + Slack API
        └── escalation.py        # APScheduler: SLA breach + reminder jobs
```

---

## Database Schema

```
users
  id, name, email, hashed_password, role(submitter|approver|admin),
  department, is_active, ooo_until, delegate_id → users.id, created_at

approver_groups
  id, name, description, created_at

approver_group_members
  id, group_id → approver_groups.id, user_id → users.id

workflows
  id, name, description, type(approval|review|acknowledgement|signature),
  folder_trigger, is_active, escalation_hours, rejection_behavior(stop|restart|escalate),
  notification_channel(email|slack|both), auto_approve_hours, amount_threshold,
  created_by_id → users.id, created_at, updated_at

workflow_stages
  id, workflow_id → workflows.id, name, type, order,
  approver_group_id → approver_groups.id, sla_hours, voting_rule(any|all|sequential),
  condition_field, condition_op, condition_value

workflow_requests
  id, title, description, document_name, document_url, amount, department,
  request_type, workflow_id → workflows.id, submitter_id → users.id,
  status(pending|approved|rejected|escalated|cancelled), current_stage,
  submitted_at, resolved_at, sla_deadline

request_stages
  id, request_id → workflow_requests.id, stage_id → workflow_stages.id,
  stage_order, status, started_at, completed_at, sla_deadline, is_sla_breached

approval_actions
  id, request_stage_id → request_stages.id, approver_id → users.id,
  decision(approved|rejected|delegated), comment, delegated_to_id → users.id, acted_at

activity_log
  id, request_id → workflow_requests.id, user_id → users.id (nullable = system),
  action, detail, created_at

webhook_configs
  id, workflow_id → workflows.id (nullable = global), event, url, secret, is_active, created_at
```

---

## Setup & Installation

### 1. MySQL

```sql
CREATE DATABASE workflow_engine CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'wfuser'@'localhost' IDENTIFIED BY 'yourpassword';
GRANT ALL PRIVILEGES ON workflow_engine.* TO 'wfuser'@'localhost';
FLUSH PRIVILEGES;
```

### 2. Environment

```bash
cd backend
cp .env.example .env   # or edit .env directly
```

At minimum set `DATABASE_URL` and `SECRET_KEY`. See [Environment Variables](#environment-variables) for all options.

### 3. Backend

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Tables are auto-created on first run via `Base.metadata.create_all()`.

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

### 4. First-time Bootstrap

```bash
# 1. Register an admin user
curl -s -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Admin","email":"admin@co.com","password":"admin123","role":"admin"}'

# 2. Log in and capture the token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@co.com","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 3. Register an approver
curl -s -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Finance Approver","email":"finance@co.com","password":"pass123","role":"approver","department":"Finance"}'

# 4. Create an approver group
curl -s -X POST http://localhost:8000/api/stages/approver-groups \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Finance Team","description":"Finance department approvers","member_ids":[2]}'

# 5. Create a workflow
curl -s -X POST http://localhost:8000/api/workflows/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Vendor Invoice Approval",
    "type": "approval",
    "escalation_hours": 24,
    "rejection_behavior": "stop",
    "notification_channel": "email",
    "amount_threshold": 10000,
    "stages": [
      {
        "name": "Finance Review",
        "type": "approval",
        "order": 1,
        "approver_group_id": 1,
        "sla_hours": 48,
        "voting_rule": "any"
      }
    ]
  }'
```

---

## Environment Variables

All variables are read from `.env` in the backend directory.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | MySQL connection string. Format: `mysql+pymysql://user:pass@host:port/db` |
| `SECRET_KEY` | — | **Required.** Random secret for JWT signing. Use `openssl rand -hex 32` |
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime in minutes |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Refresh token lifetime in days |
| `DEBUG` | `0` | Set to `1` to enable debug logging and open CORS to all origins |
| `FRONTEND_URL` | `http://localhost:3000` | Used in CORS allowlist (non-debug) and email link generation |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP server port (587 = STARTTLS) |
| `SMTP_USER` | — | SMTP login / from address. Leave blank to disable email |
| `SMTP_PASSWORD` | — | SMTP password or app password |
| `SMTP_FROM_NAME` | `Workflow Engine` | Display name in From header |
| `SLACK_BOT_TOKEN` | — | Bot User OAuth Token (`xoxb-…`). Leave blank to disable Slack |
| `SLACK_SIGNING_SECRET` | — | Used to verify incoming Slack interactive payloads |
| `WEBHOOK_SECRET` | — | HMAC secret for signing outgoing webhook payloads |
| `ESCALATION_CHECK_INTERVAL` | `15` | How often (minutes) the SLA escalation scheduler job runs |

---

## API Reference

All endpoints are prefixed with their router path. Protected endpoints require:

```
Authorization: Bearer <access_token>
```

---

### Auth

#### `POST /api/auth/register`

Register a new user. Open endpoint (no auth required).

**Request body**
```json
{
  "name": "Jane Doe",
  "email": "jane@company.com",
  "password": "secret123",
  "role": "submitter",
  "department": "Finance"
}
```

`role` must be one of: `submitter` · `approver` · `admin`

**Response `201`**
```json
{
  "id": 3,
  "name": "Jane Doe",
  "email": "jane@company.com",
  "role": "submitter",
  "department": "Finance",
  "is_active": true
}
```

---

#### `POST /api/auth/login`

Authenticate and receive an access token.

**Request body**
```json
{
  "email": "jane@company.com",
  "password": "secret123"
}
```

**Response `200`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": 3,
    "name": "Jane Doe",
    "email": "jane@company.com",
    "role": "submitter",
    "department": "Finance",
    "is_active": true
  }
}
```

---

#### `POST /api/auth/refresh`

Exchange a valid refresh token for a new access + refresh token pair.

**Request body**
```json
{
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Response `200`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

---

#### `GET /api/auth/me`

Return the currently authenticated user's profile.

**Headers** `Authorization: Bearer <token>`

**Response `200`** — same shape as `UserOut` above.

---

### Workflows

#### `GET /api/workflows/`

List all workflows. Returns all regardless of `is_active` status.

**Auth** any role

**Response `200`**
```json
[
  {
    "id": 1,
    "name": "Vendor Invoice Approval",
    "description": null,
    "type": "approval",
    "folder_trigger": "/Finance/Invoices",
    "is_active": true,
    "escalation_hours": 24,
    "rejection_behavior": "stop",
    "notification_channel": "email",
    "auto_approve_hours": null,
    "created_at": "2026-06-01T10:00:00",
    "stages": [
      {
        "id": 1,
        "name": "Finance Review",
        "type": "approval",
        "order": 1,
        "approver_group_id": 1,
        "sla_hours": 48,
        "voting_rule": "any",
        "condition_field": null,
        "condition_op": null,
        "condition_value": null
      }
    ]
  }
]
```

---

#### `GET /api/workflows/{wf_id}`

Fetch a single workflow by ID.

**Auth** any role

**Response `200`** — same shape as above (single object).

**Response `404`** — `{"detail": "Workflow not found"}`

---

#### `POST /api/workflows/`

Create a new workflow with its stages in one call.

**Auth** admin only

**Request body**
```json
{
  "name": "HR Onboarding Approval",
  "description": "Multi-stage HR document sign-off",
  "type": "approval",
  "folder_trigger": "/HR/Onboarding",
  "escalation_hours": 24,
  "rejection_behavior": "restart",
  "notification_channel": "both",
  "auto_approve_hours": null,
  "amount_threshold": 5000.00,
  "stages": [
    {
      "name": "HR Manager Review",
      "type": "approval",
      "order": 1,
      "approver_group_id": 2,
      "sla_hours": 24,
      "voting_rule": "any",
      "condition_field": null,
      "condition_op": null,
      "condition_value": null
    },
    {
      "name": "Director Sign-off",
      "type": "signature",
      "order": 2,
      "approver_group_id": 3,
      "sla_hours": 48,
      "voting_rule": "all"
    }
  ]
}
```

**Fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✓ | Workflow display name |
| `type` | enum | ✓ | `approval` · `review` · `acknowledgement` · `signature` |
| `description` | string | | Optional description |
| `folder_trigger` | string | | Path to watch for auto-submission |
| `escalation_hours` | int | | Hours before SLA escalation (default 24) |
| `rejection_behavior` | enum | | `stop` · `restart` · `escalate` (default `stop`) |
| `notification_channel` | enum | | `email` · `slack` · `both` (default `email`) |
| `amount_threshold` | float | | Requests at or below this amount are auto-approved |
| `stages` | array | | Stage definitions — see stage fields below |

**Stage fields**

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✓ | Stage display name |
| `type` | enum | ✓ | Same enum as workflow type |
| `order` | int | ✓ | Execution order (1-based, must be unique per workflow) |
| `approver_group_id` | int | ✓ | ID of the approver group for this stage |
| `sla_hours` | int | | Hours until SLA breach (default 48) |
| `voting_rule` | enum | | `any` · `all` · `sequential` (default `any`) |
| `condition_field` | string | | Field to evaluate for conditional routing: `amount` · `department` · `request_type` |
| `condition_op` | string | | Operator: `eq` · `lt` · `lte` · `gt` · `gte` · `contains` |
| `condition_value` | string | | Value to compare against |

**Response `200`** — full workflow object including generated IDs.

---

#### `PATCH /api/workflows/{wf_id}`

Partially update a workflow. Pass only the fields you want to change.

**Auth** admin only

**Request body** (all fields optional)
```json
{
  "name": "Updated Name",
  "is_active": false,
  "escalation_hours": 48,
  "stages": [ ... ]
}
```

If `stages` is included, all existing stages are deleted and replaced with the provided list.

**Response `200`** — updated workflow object.

---

#### `DELETE /api/workflows/{wf_id}`

Delete a workflow and all its stages (cascade).

**Auth** admin only

**Response `200`** — `{"detail": "Deleted"}`

---

### Stages & Approver Groups

#### `GET /api/stages/approver-groups`

List all approver groups with their members.

**Auth** any role

**Response `200`**
```json
[
  {
    "id": 1,
    "name": "Finance Team",
    "description": "Finance department approvers",
    "members": [
      { "id": 2, "name": "Finance Approver", "email": "finance@co.com", "role": "approver" }
    ]
  }
]
```

---

#### `POST /api/stages/approver-groups`

Create an approver group, optionally pre-populating members.

**Auth** admin only

**Request body**
```json
{
  "name": "Legal Team",
  "description": "Legal department reviewers",
  "member_ids": [4, 5]
}
```

**Response `200`** — created group with members array.

---

#### `DELETE /api/stages/approver-groups/{group_id}`

Delete an approver group. Stages referencing this group will lose their approver assignment.

**Auth** admin only

**Response `200`** — `{"detail": "Deleted"}`

---

#### `POST /api/stages/approver-groups/{group_id}/members`

Add a user to an existing approver group.

**Auth** admin only

**Request body**
```json
{ "user_id": 6 }
```

**Response `200`** — `{"detail": "Member added"}`

---

#### `DELETE /api/stages/approver-groups/{group_id}/members/{user_id}`

Remove a user from an approver group.

**Auth** admin only

**Response `200`** — `{"detail": "Member removed"}`

---

#### `GET /api/stages/users`

List all active users. Used when building approver groups.

**Auth** admin only

**Response `200`**
```json
[
  { "id": 2, "name": "Finance Approver", "email": "finance@co.com", "role": "approver", "department": "Finance" }
]
```

---

#### `POST /api/stages/{wf_id}/stages`

Add a single stage to an existing workflow.

**Auth** admin only

**Request body** — same shape as the stage object in workflow creation.

**Response `200`** — created stage.

---

#### `DELETE /api/stages/{stage_id}`

Delete a workflow stage by its ID.

**Auth** admin only

**Response `200`** — `{"detail": "Deleted"}`

---

### Requests

#### `POST /api/requests/upload`

Upload a document file before submitting a request. Returns the stored URL to pass into request creation.

**Auth** any role

**Request** `multipart/form-data` with field `file`.

**Response `200`**
```json
{
  "document_name": "Invoice_Q2_Infosys.pdf",
  "document_url": "/uploads/3ca56133-c9f2-4a19-aae4-f06b2f3ffbe9.pdf"
}
```

Uploaded files are served statically at `/api/uploads/<filename>`.

---

#### `POST /api/requests/`

Submit a new workflow request. Automatically starts the first stage and writes an activity log entry. If the request amount is at or below the workflow's `amount_threshold`, the request is auto-approved immediately without going through stages.

**Auth** any role

**Request body**
```json
{
  "title": "Vendor Invoice - Infosys Q2",
  "description": "Quarterly software maintenance invoice",
  "document_name": "Invoice_Q2_Infosys.pdf",
  "document_url": "/uploads/3ca56133-c9f2-4a19-aae4-f06b2f3ffbe9.pdf",
  "amount": 250000.00,
  "department": "Finance",
  "request_type": "invoice",
  "workflow_id": 1
}
```

**Response `201`** — full `RequestOut` object (see below).

---

#### `GET /api/requests/`

List requests visible to the current user.

- **Admin** — all requests
- **Approver** — requests on workflows where they are in an approver group, plus their own submissions
- **Submitter** — own submissions only

**Auth** any role

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `status` | string | Filter by status: `pending` · `approved` · `rejected` · `escalated` · `cancelled` |
| `workflow_id` | int | Filter by workflow |

**Response `200`** — array of `RequestOut` objects.

**`RequestOut` shape**
```json
{
  "id": 42,
  "title": "Vendor Invoice - Infosys Q2",
  "description": "Quarterly software maintenance invoice",
  "document_name": "Invoice_Q2_Infosys.pdf",
  "document_url": "/uploads/3ca56133.pdf",
  "amount": 250000.00,
  "department": "Finance",
  "workflow_id": 1,
  "workflow_name": "Vendor Invoice Approval",
  "submitter_id": 3,
  "submitter_name": "Jane Doe",
  "status": "pending",
  "current_stage": 1,
  "total_stages": 2,
  "submitted_at": "2026-06-11T08:00:00",
  "resolved_at": null,
  "sla_deadline": null,
  "pending_group_name": "Finance Team",
  "stages": [
    {
      "id": 7,
      "stage_id": 1,
      "stage_order": 1,
      "stage_name": "Finance Review",
      "stage_type": "approval",
      "group_name": "Finance Team",
      "voting_rule": "any",
      "status": "pending",
      "started_at": "2026-06-11T08:00:00",
      "completed_at": null,
      "sla_deadline": "2026-06-13T08:00:00",
      "is_sla_breached": false
    }
  ],
  "history": [
    {
      "id": 1,
      "action": "submitted",
      "detail": "Request submitted by Jane Doe",
      "created_at": "2026-06-11T08:00:00",
      "user_name": "Jane Doe"
    }
  ]
}
```

---

#### `GET /api/requests/{req_id}`

Fetch a single request by ID. Access rules are the same as the list endpoint.

**Auth** any role (scoped by role)

**Response `200`** — `RequestOut` object.

**Response `403`** — if the caller is not the submitter and is not in any approver group for this workflow.

---

#### `PATCH /api/requests/{req_id}/cancel`

Cancel a pending request. Only the original submitter or an admin may cancel. Requests that are already approved, rejected, escalated, or cancelled cannot be cancelled.

**Auth** submitter (own) or admin

**Response `200`** — `{"detail": "Request cancelled"}`

---

#### `GET /api/requests/action/{token}`

One-click approve or reject from an email link. No login required. The signed token (generated by `create_approval_token`) encodes the request ID, stage order, and action.

**Auth** none — token is self-contained

**URL example**
```
GET /api/requests/action/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Behavior**
1. Decodes and validates the JWT (type must be `approval_link`, expiry checked).
2. Verifies the target stage is still pending.
3. Records the approval action against the first member of the stage's approver group.
4. Calls the same stage-completion logic as a normal approval.
5. Returns the updated `RequestOut`.

**Response `200`** — updated `RequestOut`.

**Response `400`** — if the stage is no longer pending, or the action was already recorded.

**Response `401`** — if the token is invalid or expired (3-day lifetime).

---

### Approvals

#### `POST /api/approvals/`

Record an approve, reject, or delegate decision for the current stage of a request.

**Auth** approver or admin

**Request body**
```json
{
  "request_id": 42,
  "decision": "approved",
  "comment": "Looks good, proceeding.",
  "delegated_to_id": null
}
```

`decision` must be one of: `approved` · `rejected` · `delegated`

When `decision` is `delegated`, `delegated_to_id` (user ID) is required.

**Stage completion rules** (applied automatically after the action is recorded):

| Voting rule | Advance when | Reject when |
|---|---|---|
| `any` | ≥ 1 approval | ≥ 1 rejection |
| `all` | all members approved | ≥ 1 rejection |
| `sequential` | most recent action is approved | most recent action is rejected |

**Rejection behaviors** (configured per workflow):

| Behavior | Effect |
|---|---|
| `stop` | Request status → `rejected`, resolved |
| `restart` | All stages reset to pending, workflow restarts from stage 1 |
| `escalate` | Request status → `escalated`, no further stage transitions |

**Response `200`** — `ApprovalActionOut`
```json
{
  "id": 15,
  "request_stage_id": 7,
  "approver_id": 2,
  "decision": "approved",
  "comment": "Looks good, proceeding.",
  "acted_at": "2026-06-11T09:30:00"
}
```

**Response `400`** — request is not pending · stage already completed · duplicate action by same user.

**Response `403`** — user is not a member of this stage's approver group.

---

#### `GET /api/approvals/pending`

Return the IDs of requests that are currently awaiting the calling user's decision.

**Auth** any role

- **Admin** — all request IDs with status `pending`
- **Approver** — request IDs where the user is in the active stage's approver group and has not yet acted

**Response `200`**
```json
[42, 47, 51]
```

---

### Analytics

All analytics endpoints accept `?days=N` (1–365, default 30) to scope the time window.

---

#### `GET /api/analytics/summary`

High-level metrics for the selected time window.

**Auth** any role

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `days` | int | Lookback window in days (default 30, max 365) |
| `workflow_id` | int | Scope to a single workflow |

**Response `200`**
```json
{
  "total_requests": 120,
  "pending": 18,
  "approved": 85,
  "rejected": 12,
  "escalated": 5,
  "approval_rate": 70.8,
  "avg_resolution_hours": 11.4,
  "sla_breaches": 7,
  "recent_activity": [
    {
      "id": 201,
      "request_id": 42,
      "action": "approved",
      "detail": "Stage 1 approved by Finance Approver.",
      "created_at": "2026-06-11T09:30:00",
      "user": {
        "id": 2,
        "name": "Finance Approver",
        "email": "finance@co.com",
        "role": "approver",
        "department": "Finance",
        "is_active": true
      }
    }
  ]
}
```

---

#### `GET /api/analytics/by-workflow`

Approval metrics broken down per workflow, sorted by total request volume descending.

**Auth** any role

**Query parameters** — `days` (same as summary)

**Response `200`**
```json
[
  {
    "workflow_id": 1,
    "workflow": "Vendor Invoice Approval",
    "total": 60,
    "approved": 48,
    "rejected": 8,
    "approval_rate_pct": 80.0
  },
  {
    "workflow_id": 2,
    "workflow": "HR Onboarding Approval",
    "total": 30,
    "approved": 22,
    "rejected": 4,
    "approval_rate_pct": 73.3
  }
]
```

Workflows with zero requests in the period are omitted.

---

#### `GET /api/analytics/approver-performance`

Per-approver decision counts and average response time, sorted by total decisions descending.

**Auth** any role

**Query parameters** — `days` (same as summary)

**Response `200`**
```json
[
  {
    "approver_id": 2,
    "approver": "Finance Approver",
    "email": "finance@co.com",
    "total_decisions": 45,
    "approved": 38,
    "rejected": 5,
    "delegated": 2,
    "avg_response_hours": 3.2
  }
]
```

`avg_response_hours` is measured from the stage's `started_at` to the approver's `acted_at`. `null` if no timing data is available.

---

#### `GET /api/analytics/activity-feed`

Recent audit trail entries across all requests, with document and actor names resolved.

**Auth** any role

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `limit` | int | Max entries to return (default 50, max 200) |

**Response `200`**
```json
[
  {
    "id": 201,
    "action": "approved",
    "document_name": "Invoice_Q2_Infosys.pdf",
    "request_title": "Vendor Invoice - Infosys Q2",
    "actor_name": "Finance Approver",
    "detail": "Stage 1 approved by Finance Approver. Looks good.",
    "created_at": "2026-06-11T09:30:00"
  }
]
```

`actor_name` is `"System"` for automated actions (escalations, auto-approvals, reminders).

---

## Authentication

The API uses JWT Bearer tokens.

**Access tokens** — short-lived (60 min by default). Send in the `Authorization` header:
```
Authorization: Bearer <access_token>
```

**Refresh tokens** — long-lived (7 days). Use `POST /api/auth/refresh` to get a new access token without re-entering credentials. Store refresh tokens securely (httpOnly cookie recommended for web clients).

**Approval link tokens** — single-use 3-day tokens embedded in notification emails. They carry `request_id`, `stage_order`, and `action` in the payload and are verified server-side before recording the decision. No `Authorization` header needed.

---

## Role-Based Access

| Endpoint category | submitter | approver | admin |
|---|---|---|---|
| Register / login / refresh / me | ✓ | ✓ | ✓ |
| List/view workflows | ✓ | ✓ | ✓ |
| Create/update/delete workflows | — | — | ✓ |
| Submit a request | ✓ | ✓ | ✓ |
| View own requests | ✓ | ✓ | ✓ |
| View requests on their workflows | — | ✓ | ✓ |
| View all requests | — | — | ✓ |
| Cancel own request | ✓ | ✓ | ✓ |
| Cancel any request | — | — | ✓ |
| Approve/reject/delegate | — | ✓ | ✓ |
| View pending approvals | ✓ (empty) | ✓ | ✓ |
| Approver group CRUD | — | — | ✓ |
| User list | — | — | ✓ |
| Analytics | ✓ | ✓ | ✓ |

---

## Workflow Engine Logic

### Request submission flow

```
POST /api/requests/
  │
  ├─ amount <= workflow.amount_threshold?
  │     └─ YES → status = approved, resolved_at = now, ActivityLog: auto_approved
  │
  └─ NO → Create RequestStage rows for all stages
            Start stage 1: started_at = now, sla_deadline = now + stage.sla_hours
            ActivityLog: submitted
```

### Stage completion (on each approval action)

```
_check_stage_completion(db, request_stage, request)
  │
  ├─ Any rejection?
  │     ├─ rejection_behavior = stop     → stage + request status = rejected
  │     ├─ rejection_behavior = restart  → reset all stages, restart from stage 1
  │     └─ rejection_behavior = escalate → stage + request status = escalated
  │
  └─ No rejections → check voting rule
        ├─ any        → approved_count >= 1 → stage approved → advance
        ├─ all        → approved_count == group_member_count → advance
        └─ sequential → last action approved → advance
                        last action rejected → apply rejection_behavior

_advance_request(db, request)
  ├─ next pending RequestStage exists?
  │     └─ YES → current_stage = next_stage.order
  │               next_stage.started_at = now
  │               next_stage.sla_deadline = now + stage.sla_hours
  │
  └─ NO → request.status = approved, resolved_at = now
```

### Status values

| Status | Meaning |
|---|---|
| `pending` | Awaiting action at the current stage |
| `approved` | All stages passed, workflow complete |
| `rejected` | Rejected at a stage (and `rejection_behavior = stop`) |
| `escalated` | SLA breached or `rejection_behavior = escalate` triggered |
| `cancelled` | Cancelled by submitter or admin before completion |

---

## Notifications & Webhooks

### Email notifications (`services/notification.py`)

Triggered manually or by the scheduler. Configure SMTP credentials in `.env`.

- **`notify_approvers()`** — sends an HTML email with inline Approve / Reject buttons to each member of the stage's approver group. Each button links to `GET /api/requests/action/<token>`.
- **`notify_submitter_completed()`** — notifies the submitter when their request is fully approved or rejected.
- **`send_email()`** — generic helper used by the escalation job for admin alerts.

### Slack notifications

Configure `SLACK_BOT_TOKEN` in `.env`.

- **`notify_slack_approver()`** — sends a Block Kit message with Approve/Reject buttons to the approver's DM channel. Button clicks are handled by your Slack app's interactive endpoint (implement separately using `webhook_utils.slack_blocks()`).
- **`send_slack()`** — generic helper for arbitrary channels.

### Outgoing webhooks (`webhook_utils.py`)

Call `fire_webhook(db, event, request)` after any state change to dispatch to all matching `WebhookConfig` rows.

**Supported events**

| Event | Trigger |
|---|---|
| `request.submitted` | New request created |
| `stage.approved` | A stage was approved |
| `stage.rejected` | A stage was rejected |
| `stage.escalated` | SLA breach escalation |
| `request.approved` | Final approval, workflow complete |
| `request.rejected` | Rejection with `stop` behavior |
| `request.cancelled` | Request cancelled |

**Payload envelope**
```json
{
  "event": "stage.approved",
  "timestamp": 1718000000,
  "request": {
    "id": 42,
    "title": "Vendor Invoice - Infosys Q2",
    "status": "pending",
    "current_stage": 1,
    "workflow_id": 1,
    "submitted_at": "2026-06-11T08:00:00",
    "document_name": "Invoice_Q2_Infosys.pdf",
    "amount": 250000.00,
    "department": "Finance"
  }
}
```

When a `WebhookConfig.secret` is set, the payload is signed and delivered with:
```
X-Signature-256: sha256=<hmac-sha256-hex>
X-Workflow-Event: stage.approved
```

---

## Background Jobs (Scheduler)

The scheduler starts automatically with the FastAPI app (via `lifespan`) and stops cleanly on shutdown.

### Escalation job — runs every `ESCALATION_CHECK_INTERVAL` minutes (default 15)

1. Queries `RequestStage` rows where `status = pending`, `sla_deadline <= now`, `is_sla_breached = false`, and `started_at IS NOT NULL`.
2. Marks each as `is_sla_breached = true`.
3. Sets the parent `WorkflowRequest.status = escalated`.
4. Writes an `ActivityLog` entry with action `escalated`.
5. Sends an admin alert email listing the breached request.

### Reminder job — runs every hour

1. Queries `RequestStage` rows where `sla_deadline` is between now and now + 4 hours, not yet breached, and `started_at IS NOT NULL`.
2. For each stage, resolves the approver group members.
3. Sends a reminder email to each member with a link to the request.

To change the escalation interval, update `ESCALATION_CHECK_INTERVAL` in `.env` and restart.

---

## Error Responses

All errors follow a standard shape:

```json
{ "detail": "Human-readable error message" }
```

| HTTP status | Meaning |
|---|---|
| `400` | Bad request — invalid input, duplicate action, wrong state |
| `401` | Unauthorized — missing, invalid, or expired token |
| `403` | Forbidden — authenticated but insufficient role or group membership |
| `404` | Not found — resource does not exist |
| `500` | Internal server error — unhandled exception (logged server-side) |

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI 0.111, SQLAlchemy 2.0, Alembic, PyMySQL |
| Auth | python-jose (JWT), passlib (bcrypt) |
| Email | aiosmtplib 3.0 |
| Scheduler | APScheduler 3.10 |
| HTTP client | httpx 0.27 |
| Frontend | React 18, Vite 5 |
| Styling | Pure CSS custom properties |
| DB | MySQL 8+ |

---

## Roadmap

### High priority
- [ ] Wire `notify_approvers()` into `routers/approvals.py` after stage start
- [ ] Wire `notify_submitter_completed()` after final approval/rejection
- [ ] Approver group management UI (currently API-only)
- [ ] Delegation UI — OOO toggle + delegate selector on user profile page
- [ ] Conditional branching UI — stage-level condition editor

### Nice to have
- [ ] Alembic migration files for schema versioning
- [ ] S3 / MinIO document storage (replace local `uploads/`)
- [ ] Bulk approve from approval queue
- [ ] Slack interactive button endpoint (`POST /api/webhooks/slack/actions`)
- [ ] Role-based nav hiding on the frontend
- [ ] Request detail drawer with full audit trail
