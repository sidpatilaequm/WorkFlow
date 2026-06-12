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
10. [Stage Types & Email Actions](#stage-types--email-actions)
11. [Notifications & Webhooks](#notifications--webhooks)
12. [Background Jobs (Scheduler)](#background-jobs-scheduler)
13. [Error Responses](#error-responses)
14. [Tech Stack](#tech-stack)
15. [Changelog](#changelog)

---

## Features

| Module | What's built |
|---|---|
| **Auth** | JWT login/register, access + refresh tokens, role-based access (submitter / approver / admin) |
| **Workflows** | CRUD, 4 stage types (approval / review / acknowledgement / signature), folder triggers, amount-based auto-approve |
| **Stages** | Per-stage SLA deadlines, voting rules (any / all / sequential), per-stage conditional branching |
| **Requests** | Submit with document upload, cancel, track with live stage progress, one-click email approval |
| **Approvals** | Approve / reject / delegate, rejection behaviors (stop / restart / escalate), duplicate prevention |
| **Analytics** | Approval rate, avg resolution time, SLA breach count, by-workflow breakdown, approver performance, activity feed |
| **Notifications** | Async SMTP email with stage-type-aware action buttons, Slack Block Kit DMs |
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
    ├── auth_utils.py            # JWT (access + refresh + approval tokens w/ approver_id), bcrypt, role guards
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
  id, workflow_id → workflows.id, name, type(approval|review|acknowledgement|signature),
  order, approver_group_id → approver_groups.id, sla_hours,
  voting_rule(any|all|sequential), condition_field, condition_op, condition_value

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

**Response `200`** — same shape as `UserOut` above.

---

### Workflows

#### `GET /api/workflows/`

List all workflows.

**Auth** any role

**Response `200`** — array of workflow objects including their stages.

---

#### `GET /api/workflows/{wf_id}`

Fetch a single workflow by ID.

**Response `404`** — `{"detail": "Workflow not found"}`

---

#### `POST /api/workflows/`

Create a new workflow with its stages in one call.

**Auth** admin only

**Request body**
```json
{
  "name": "HR Onboarding Approval",
  "type": "approval",
  "escalation_hours": 24,
  "rejection_behavior": "restart",
  "notification_channel": "both",
  "amount_threshold": 5000.00,
  "stages": [
    {
      "name": "HR Manager Review",
      "type": "review",
      "order": 1,
      "approver_group_id": 2,
      "sla_hours": 24,
      "voting_rule": "any"
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

**Stage types** — each stage independently sets its own type, which drives email button labels (see [Stage Types & Email Actions](#stage-types--email-actions)).

**Voting rules** — `any` (one approval suffices) · `all` (every group member must act) · `sequential` (last action wins).

When `voting_rule` is `all`, each group member receives their own individually signed email link. The backend records each approval against that specific member. The stage only completes once `approved_count == group_member_count`.

**Response `200`** — full workflow object with generated IDs.

---

#### `PATCH /api/workflows/{wf_id}`

Partially update a workflow. If `stages` is included, all existing stages are replaced.

**Auth** admin only

---

#### `DELETE /api/workflows/{wf_id}`

Delete a workflow and all its stages (cascade).

**Auth** admin only

**Response `200`** — `{"detail": "Deleted"}`

---

### Stages & Approver Groups

#### `GET /api/stages/approver-groups`

List all approver groups with their members.

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

```json
{ "name": "Legal Team", "description": "Legal department reviewers", "member_ids": [4, 5] }
```

---

#### `DELETE /api/stages/approver-groups/{group_id}`

Delete an approver group. **Auth** admin only.

---

#### `POST /api/stages/approver-groups/{group_id}/members`

Add a user to a group. **Auth** admin only. Body: `{ "user_id": 6 }`

---

#### `DELETE /api/stages/approver-groups/{group_id}/members/{user_id}`

Remove a user from a group. **Auth** admin only.

---

#### `GET /api/stages/users`

List all active users. **Auth** admin only.

---

### Requests

#### `POST /api/requests/upload`

Upload a document before submitting a request.

**Request** `multipart/form-data` with field `file`.

**Response `200`**
```json
{
  "document_name": "Invoice_Q2_Infosys.pdf",
  "document_url": "/uploads/3ca56133-c9f2-4a19-aae4-f06b2f3ffbe9.pdf"
}
```

Files are served statically at `/api/uploads/<filename>`.

---

#### `POST /api/requests/`

Submit a new workflow request. Starts stage 1 immediately and emails all members of that stage's approver group.

If `amount <= workflow.amount_threshold`, the request is auto-approved without going through stages.

**Request body**
```json
{
  "title": "Vendor Invoice - Infosys Q2",
  "description": "Quarterly software maintenance invoice",
  "document_name": "Invoice_Q2_Infosys.pdf",
  "document_url": "/uploads/3ca56133.pdf",
  "amount": 250000.00,
  "department": "Finance",
  "request_type": "invoice",
  "workflow_id": 1
}
```

**Response `201`** — full `RequestOut` object.

---

#### `GET /api/requests/`

List requests visible to the current user (scoped by role). Supports `?status=` and `?workflow_id=` filters.

**`RequestOut` shape**
```json
{
  "id": 42,
  "title": "Vendor Invoice - Infosys Q2",
  "workflow_id": 1,
  "workflow_name": "Vendor Invoice Approval",
  "submitter_id": 3,
  "submitter_name": "Jane Doe",
  "status": "pending",
  "current_stage": 1,
  "total_stages": 2,
  "submitted_at": "2026-06-11T08:00:00",
  "resolved_at": null,
  "pending_group_name": "Finance Team",
  "stages": [
    {
      "id": 7,
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

Fetch a single request. Access is scoped: submitters see their own, approvers see requests on their workflows.

**Response `403`** — if the caller is not the submitter and is not in any approver group for this workflow.

---

#### `PATCH /api/requests/{req_id}/cancel`

Cancel a pending request. Only the submitter or an admin may cancel.

**Response `200`** — `{"detail": "Request cancelled"}`

---

#### `GET /api/requests/action/{token}`

One-click action handler for email links. No login required.

The token encodes `request_id`, `stage_order`, `action` (`approved` or `rejected`), and `approver_id`. This ensures that in `voting_rule = all` stages, each member's click is recorded against their own identity rather than a shared placeholder.

**Behavior**
1. Decode and validate the JWT (`type = approval_link`, expiry checked).
2. Verify the target stage is still `pending`.
3. Resolve the acting member from `approver_id` in the token (falls back to first group member for legacy tokens).
4. Check for a duplicate action by that specific member.
5. Write `ApprovalAction` + `ActivityLog`.
6. Run `_check_stage_completion()` — same logic as the in-app approve flow.
7. Return the updated `RequestOut`.

**Response `200`** — updated `RequestOut`.

**Response `400`** — stage no longer pending, or action already recorded for this member.

**Response `401`** — token invalid or expired (3-day lifetime).

---

### Approvals

#### `POST /api/approvals/`

Record an approve, reject, or delegate decision for the current stage.

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

**Stage completion rules**

| Voting rule | Advance when | Reject when |
|---|---|---|
| `any` | ≥ 1 approval | ≥ 1 rejection |
| `all` | approved_count == group_member_count | ≥ 1 rejection |
| `sequential` | most recent action is approved | most recent action is rejected |

**Rejection behaviors**

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

---

#### `GET /api/approvals/pending`

Return request IDs awaiting the calling user's decision.

- **Admin** — all pending request IDs
- **Approver** — IDs where they are in the active stage's group and have not yet acted

**Response `200`** — `[42, 47, 51]`

---

### Analytics

All analytics endpoints accept `?days=N` (1–365, default 30).

#### `GET /api/analytics/summary` — high-level metrics
#### `GET /api/analytics/by-workflow` — per-workflow breakdown
#### `GET /api/analytics/approver-performance` — per-approver decision counts and avg response time
#### `GET /api/analytics/activity-feed` — recent audit trail (`?limit=N`, max 200)

---

## Authentication

**Access tokens** — 60 min by default. Send as `Authorization: Bearer <token>`.

**Refresh tokens** — 7 days. Use `POST /api/auth/refresh` to renew without re-login.

**Approval link tokens** — 3-day, signed JWT embedded in notification emails. Carry `request_id`, `stage_order`, `action`, and `approver_id`. Verified server-side; no `Authorization` header needed.

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
| Approve / reject / delegate | — | ✓ | ✓ |
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
            Email all members of stage 1's approver group (one personal token per member)
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
        ├─ any        → approved_count >= 1                  → stage approved → advance
        ├─ all        → approved_count == group_member_count → stage approved → advance
        └─ sequential → last action approved → advance
                        last action rejected → apply rejection_behavior

_advance_request(db, request)
  ├─ next pending RequestStage exists?
  │     └─ YES → current_stage = next_stage.order
  │               next_stage.started_at = now
  │               next_stage.sla_deadline = now + stage.sla_hours
  │               Email all members of next stage's approver group
  │
  └─ NO → request.status = approved, resolved_at = now
           Email submitter: notify_submitter_completed()
```

### Status values

| Status | Meaning |
|---|---|
| `pending` | Awaiting action at the current stage |
| `approved` | All stages passed, workflow complete |
| `rejected` | Rejected at a stage (rejection_behavior = stop) |
| `escalated` | SLA breached or rejection_behavior = escalate triggered |
| `cancelled` | Cancelled by submitter or admin before completion |

---

## Stage Types & Email Actions

Each workflow stage has a `type` that independently controls what the email buttons say and what the subject line reads. The backend maps `stage_type` → labels in `NotificationService.STAGE_TYPE_LABELS`.

| Stage type | Positive button | Negative button | Subject prefix |
|---|---|---|---|
| `approval` | ✓ Approve | ✗ Reject | `[Approval Required]` |
| `review` | ✓ Mark Reviewed | ✗ Request Changes | `[Review Required]` |
| `acknowledgement` | ✓ Acknowledge | ✗ Decline | `[Acknowledgement Required]` |
| `signature` | ✓ Sign | ✗ Refuse | `[Signature Required]` |

The stage type is read from `WorkflowStage.type` at notification time and passed into `notify_approvers(stage_type=...)`. An unknown type falls back to the `approval` labels.

### Per-member tokens (voting_rule = all)

When a stage uses `voting_rule = all`, each group member receives their **own personally addressed email** with links that contain their `approver_id` embedded in the JWT. This means:

- Every member must click their own link — no shared link that one person can click for everyone.
- The duplicate-action guard is per-member, so one person clicking twice is blocked, but it does not block anyone else.
- The stage only advances once `approved_count == group_member_count`.

---

## Notifications & Webhooks

### Email notifications (`services/notification.py`)

All email sending is async (`aiosmtplib`). In sync FastAPI route handlers, notifications are dispatched via `_run_async()` — a daemon thread calling `asyncio.run()` — to avoid conflicts with uvicorn's event loop.

- **`notify_approvers()`** — sends a personalised HTML email to each approver. Button labels and subject line adapt to the stage type. Each email carries that approver's unique signed token.
- **`notify_submitter_completed()`** — notifies the submitter when the request is fully approved or rejected.
- **`send_email()`** — generic SMTP helper. Silently skips if `SMTP_USER` / `SMTP_PASSWORD` are not configured.

### Slack notifications

Configure `SLACK_BOT_TOKEN` in `.env`.

- **`notify_slack_approver()`** — sends a Block Kit DM with Approve / Reject buttons.
- **`send_slack()`** — generic helper for arbitrary channels.

### Outgoing webhooks (`webhook_utils.py`)

**Supported events:** `request.submitted` · `stage.approved` · `stage.rejected` · `stage.escalated` · `request.approved` · `request.rejected` · `request.cancelled`

Payloads are HMAC-SHA256 signed when a `WebhookConfig.secret` is set:
```
X-Workflow-Signature: sha256=<hmac-hex>
X-Workflow-Event: stage.approved
```

---

## Background Jobs (Scheduler)

Starts automatically with the FastAPI app (via `lifespan`) and stops cleanly on shutdown.

### Escalation job — every `ESCALATION_CHECK_INTERVAL` minutes (default 15)

1. Finds `RequestStage` rows where `status = pending`, `sla_deadline <= now`, `is_sla_breached = false`.
2. Marks `is_sla_breached = true`, sets parent request to `escalated`.
3. Writes `ActivityLog: escalated`.
4. Sends an admin alert email.

### Reminder job — every hour

1. Finds stages with `sla_deadline` between now and now + 4 hours, not yet breached.
2. Sends a reminder email to each group member.

---

## Error Responses

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

## Changelog

### June 2026

**Email action flow — per-member tokens**
- `create_approval_token()` now accepts an `approver_id` parameter and embeds it in the JWT payload.
- `_fire_stage_notification()` generates one personal token pair per group member and sends individual emails, rather than one shared link for the whole group.
- `one_click_action()` reads `approver_id` from the token and resolves the acting member precisely, so `voting_rule = all` stages correctly accumulate one approval per person.

**Stage-type-aware email buttons**
- `notify_approvers()` now accepts a `stage_type` parameter.
- Button labels and email subject line adapt per stage type: Approve/Reject for `approval`, Mark Reviewed/Request Changes for `review`, Acknowledge/Decline for `acknowledgement`, Sign/Refuse for `signature`.
- `_fire_stage_notification()` passes `stage_def.type.value` through to the notification service.

**First-stage notification on submit**
- `submit_request()` now calls `_fire_stage_notification()` after `db.commit()` for stage 1. Previously, approvers were only emailed when a stage _advanced_ (stage 2+), so the first stage never triggered emails.

**Async email in sync handlers**
- Replaced `asyncio.create_task()` calls (which fail in sync route handlers running in a thread pool) with `_run_async()` — a helper that fires a daemon thread and calls `asyncio.run(coro)` inside it.

