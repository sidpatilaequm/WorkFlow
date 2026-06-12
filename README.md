# WorkflowOS — Document Approval Workflow Engine

Full-stack: **FastAPI + MySQL** backend · **React + Vite** frontend.

> **Auth model:** Registration and JWT middleware have been removed. All protected endpoints accept `?user_id=<id>` as a required query parameter. The caller is responsible for passing the correct user ID (from your existing users table or session management layer).

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
7. [How user_id Auth Works](#how-user_id-auth-works)
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
| **Auth** | Login only (no register). Identity passed via `?user_id=` query param on all routes |
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
    ├── auth_utils.py            # Approval link JWT tokens, bcrypt helpers
    ├── webhook_utils.py         # Outgoing webhook dispatcher + Slack Block Kit builder
    ├── requirements.txt
    ├── alembic.ini
    ├── .env                     # Environment config (never commit secrets)
    ├── uploads/                 # Uploaded document files (served at /api/uploads/)
    ├── routers/
    │   ├── auth.py              # POST /api/auth/login  GET /api/auth/me
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

> All tables — including the client-owned `user_details` table — reside in the **`multimedia_governance`** schema. SQLAlchemy is configured to use this schema globally via `MetaData(schema="multimedia_governance")` in `database.py`.

```
multimedia_governance.user_details  ← pre-existing client table, not created by this project's SQL script
  userId (PK), email, firstName, lastName, phoneNumber, designation,
  onboardingStatus, onboardingToken, tokenExpiry, userType,
  signupDate, created_date, modified_date,
  super_admin_id → (SuperAdmin FK), company_id → (CompanyDetails FK)

multimedia_governance.approver_groups
  id, name, description, created_at

multimedia_governance.approver_group_members
  id, group_id → approver_groups.id, user_id → user_details.userId

multimedia_governance.workflows
  id, name, description, type(approval|review|acknowledgement|signature),
  folder_trigger, is_active, escalation_hours, rejection_behavior(stop|restart|escalate),
  notification_channel(email|slack|both), auto_approve_hours, amount_threshold,
  created_by_id → user_details.userId, created_at, updated_at

multimedia_governance.workflow_stages
  id, workflow_id → workflows.id, name, type(approval|review|acknowledgement|signature),
  order, approver_group_id → approver_groups.id, sla_hours,
  voting_rule(any|all|sequential), condition_field, condition_op, condition_value

multimedia_governance.workflow_requests
  id, title, description, document_name, document_url, amount, department,
  request_type, workflow_id → workflows.id, submitter_id → user_details.userId,
  status(pending|approved|rejected|escalated|cancelled), current_stage,
  submitted_at, resolved_at, sla_deadline

multimedia_governance.request_stages
  id, request_id → workflow_requests.id, stage_id → workflow_stages.id,
  stage_order, status, started_at, completed_at, sla_deadline, is_sla_breached

multimedia_governance.approval_actions
  id, request_stage_id → request_stages.id, approver_id → user_details.userId,
  decision(approved|rejected|delegated), comment, delegated_to_id → user_details.userId, acted_at

multimedia_governance.activity_log
  id, request_id → workflow_requests.id, user_id → user_details.userId (nullable = system),
  action, detail, created_at

multimedia_governance.webhook_configs
  id, workflow_id → workflows.id (nullable = global), event, url, secret, is_active, created_at
```

---

## Setup & Installation

### 1. MySQL / PostgreSQL

The application uses the client's existing **`multimedia_governance`** schema. Ensure the schema and the `user_details` table already exist before running the SQL script.

```sql
-- If the schema does not already exist:
CREATE SCHEMA IF NOT EXISTS multimedia_governance;
```

Then run the SQL script at the bottom of this file against your database. It creates all workflow tables **inside** `multimedia_governance`, and references `multimedia_governance.user_details.userId` as the user foreign key.

### 2. Environment

```bash
cd backend
cp .env.example .env   # or edit .env directly
```

Set at minimum `DATABASE_URL` and `SECRET_KEY`. See [Environment Variables](#environment-variables).

### 3. Backend

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Tables are auto-created on first run via `Base.metadata.create_all()`, or you can apply `schema.sql` manually.

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

### 4. Quick Bootstrap (using existing user IDs)

Since registration is removed, seed your users directly in the database or through your existing user management system. Then use their IDs in all API calls.

```bash
# Create an approver group (user_id=1 must be an admin)
curl -s -X POST "http://localhost:8000/api/stages/approver-groups?user_id=1" \
  -H "Content-Type: application/json" \
  -d '{"name":"Finance Team","description":"Finance department approvers","member_ids":[2]}'

# Create a workflow
curl -s -X POST "http://localhost:8000/api/workflows/?user_id=1" \
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

# Submit a request (user_id=3 is the submitter)
curl -s -X POST "http://localhost:8000/api/requests/?user_id=3" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Vendor Invoice Q2",
    "amount": 250000,
    "workflow_id": 1 //Chnagesbased on the previis query
  }'
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | MySQL/PostgreSQL connection string. Format: `mysql+pymysql://user:pass@host:port/db` or `postgresql+psycopg2://user:pass@host:port/db`. The `multimedia_governance` schema is set at the ORM level. |
| `SECRET_KEY` | — | **Required.** Random secret for approval-link JWT signing. Use `openssl rand -hex 32` |
| `ALGORITHM` | `HS256` | JWT signing algorithm (used for email approval tokens only) |
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

> `ACCESS_TOKEN_EXPIRE_MINUTES` and `REFRESH_TOKEN_EXPIRE_DAYS` are no longer used since JWT middleware has been removed. `SECRET_KEY` is still required for signing email approval-link tokens.

---

## API Reference

### How `user_id` Auth Works

Every endpoint (except the email one-click action handler and `/health`) requires a `?user_id=<integer>` query parameter. The backend looks up the user in the database and enforces role checks from there. There is no `Authorization` header needed.

```
GET /api/workflows/?user_id=1
POST /api/requests/?user_id=3
POST /api/approvals/?user_id=2
```

If the user is not found or inactive, the endpoint returns `404 User not found`.

---

### Auth

> `POST /api/auth/register` has been **removed**. Users must be created directly in the database or via your existing user management system.

#### `POST /api/auth/login`

Authenticate and get back the user object. Useful for verifying credentials from a frontend login form. Does **not** require a JWT on subsequent calls — the returned user `id` should be stored client-side and passed as `?user_id=` on all further requests.

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
  "access_token": "",
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

> The `access_token` field is present for schema compatibility but is not required for subsequent calls.

---

#### `GET /api/auth/me?user_id=<id>`

Return the user profile for the given `user_id`.

**Response `200`**
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

### Workflows

All workflow endpoints require `?user_id=<id>`.

#### `GET /api/workflows/?user_id=<id>`

List all workflows including their stages. Any active user can call this.

**Response `200`** — array of workflow objects.

---

#### `GET /api/workflows/{wf_id}?user_id=<id>`

Fetch a single workflow by ID.

**Response `404`** — `{"detail": "Workflow not found"}`

---

#### `POST /api/workflows/?user_id=<id>`

Create a new workflow with its stages in one call. Requires `role = admin`.

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

**Workflow types:** `approval` · `review` · `acknowledgement` · `signature`

**Voting rules:** `any` · `all` · `sequential`

**Response `200`** — full workflow object with generated IDs.

---

#### `PATCH /api/workflows/{wf_id}?user_id=<id>`

Partially update a workflow. If `stages` is included, all existing stages are replaced. Requires `role = admin`.

---

#### `DELETE /api/workflows/{wf_id}?user_id=<id>`

Delete a workflow and all its stages (cascade). Requires `role = admin`.

**Response `200`** — `{"detail": "Deleted"}`

---

### Stages & Approver Groups

#### `GET /api/stages/approver-groups?user_id=<id>`

List all approver groups with their members. Any active user can call this.

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

#### `POST /api/stages/approver-groups?user_id=<id>`

Create an approver group, optionally pre-populating members. Requires `role = admin`.

```json
{ "name": "Legal Team", "description": "Legal department reviewers", "member_ids": [4, 5] }
```

---

#### `DELETE /api/stages/approver-groups/{group_id}?user_id=<id>`

Delete an approver group. Requires `role = admin`.

---

#### `POST /api/stages/approver-groups/{group_id}/members?user_id=<id>`

Add a user to a group. Requires `role = admin`. Body: `{ "user_id": 6 }`

---

#### `DELETE /api/stages/approver-groups/{group_id}/members/{user_id_path}?user_id=<id>`

Remove a user from a group. Requires `role = admin`.

---

#### `GET /api/stages/users?user_id=<id>`

List all active users. Requires `role = admin`.

---

#### `POST /api/stages/{wf_id}/stages?user_id=<id>`

Add a single stage to an existing workflow. Requires `role = admin`.

---

#### `DELETE /api/stages/{stage_id}?user_id=<id>`

Delete a workflow stage by its stage ID. Requires `role = admin`.

---

### Requests

#### `POST /api/requests/upload?user_id=<id>`

Upload a document before submitting a request.

**Request:** `multipart/form-data` with field `file`.

**Response `200`**
```json
{
  "document_name": "Invoice_Q2_Infosys.pdf",
  "document_url": "/uploads/3ca56133-c9f2-4a19-aae4-f06b2f3ffbe9.pdf"
}
```

Files are served statically at `/api/uploads/<filename>`.

---

#### `POST /api/requests/?user_id=<id>`

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

#### `GET /api/requests/?user_id=<id>`

List requests scoped by the caller's role. Supports `?status=` and `?workflow_id=` filters.

- **admin** — sees all requests
- **approver** — sees requests on workflows where they are a group member, plus their own submitted requests
- **submitter** — sees only their own submitted requests

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

#### `GET /api/requests/{req_id}?user_id=<id>`

Fetch a single request. Access is scoped: submitters see their own, approvers see requests on their workflows, admins see all.

**Response `403`** — if not the submitter and not in any approver group for this workflow.

---

#### `PATCH /api/requests/{req_id}/cancel?user_id=<id>`

Cancel a pending request. Only the submitter or an admin may cancel.

**Response `200`** — `{"detail": "Request cancelled"}`

---

#### `GET /api/requests/action/{req_id}`

Approves the rwquest through the email itself 

**Behavior**
1. Decode and validate the JWT (`type = approval_link`, expiry checked).
2. Verify the target stage is still `pending`.
3. Resolve the acting member from `approver_id` in the token.
4. Check for a duplicate action by that specific member.
5. Write `ApprovalAction` + `ActivityLog`.
6. Run `_check_stage_completion()`.
7. Return the updated `RequestOut`.

**Response `200`** — updated `RequestOut`.

**Response `400`** — stage no longer pending, or action already recorded for this member.

**Response `401`** — token invalid or expired (3-day lifetime).

---

### Approvals

#### `POST /api/approvals/?user_id=<id>`

Record an approve, reject, or delegate decision for the current stage. Requires `role = approver` or `admin`.

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

#### `GET /api/approvals/pending?user_id=<id>`

Return request IDs awaiting the calling user's decision.

- **admin** — all pending request IDs
- **approver** — IDs where they are in the active stage's group and have not yet acted

**Response `200`** — `[42, 47, 51]`

---

### Analytics

All analytics endpoints require `?user_id=<id>` and accept `?days=N` (1–365, default 30).

#### `GET /api/analytics/summary?user_id=<id>` — high-level metrics
#### `GET /api/analytics/by-workflow?user_id=<id>` — per-workflow breakdown
#### `GET /api/analytics/approver-performance?user_id=<id>` — per-approver decision counts and avg response time
#### `GET /api/analytics/activity-feed?user_id=<id>` — recent audit trail (`?limit=N`, max 200)

---

## Role-Based Access

| Endpoint category | submitter | approver | admin |
|---|---|---|---|
| Login / me | ✓ | ✓ | ✓ |
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
| Email one-click action | no auth required | no auth required | no auth required |

---

## Workflow Engine Logic

### Request submission flow

```
POST /api/requests/?user_id=<id>
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

| Stage type | Positive button | Negative button | Subject prefix |
|---|---|---|---|
| `approval` | ✓ Approve | ✗ Reject | `[Approval Required]` |
| `review` | ✓ Mark Reviewed | ✗ Request Changes | `[Review Required]` |
| `acknowledgement` | ✓ Acknowledge | ✗ Decline | `[Acknowledgement Required]` |
| `signature` | ✓ Sign | ✗ Refuse | `[Signature Required]` |

When `voting_rule = all`, each group member receives their own individually signed email link. Each member's click is recorded against their specific identity. The stage only completes once `approved_count == group_member_count`.

---

## Notifications & Webhooks

### Email (`services/notification.py`)

All email sending is async (`aiosmtplib`). In sync FastAPI handlers, notifications are dispatched via `_run_async()` — a daemon thread calling `asyncio.run()`.

- **`notify_approvers()`** — personalised HTML email per approver, button labels adapt to stage type.
- **`notify_submitter_completed()`** — notifies submitter on full approval or rejection.
- **`send_email()`** — silently skips if `SMTP_USER` / `SMTP_PASSWORD` are not configured.

### Slack

Configure `SLACK_BOT_TOKEN` in `.env`. `notify_slack_approver()` sends a Block Kit DM with Approve / Reject buttons.

### Outgoing Webhooks (`webhook_utils.py`)

**Supported events:** `request.submitted` · `stage.approved` · `stage.rejected` · `stage.escalated` · `request.approved` · `request.rejected` · `request.cancelled`

Payloads are HMAC-SHA256 signed when `WebhookConfig.secret` is set:
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
3. Writes `ActivityLog: escalated`, sends admin alert email.

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
| `401` | Unauthorized — invalid or expired approval-link token |
| `403` | Forbidden — insufficient role or group membership |
| `404` | Not found — user or resource does not exist |
| `500` | Internal server error — unhandled exception (logged server-side) |

---

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI 0.111, SQLAlchemy 2.0, Alembic, PyMySQL |
| Auth (tokens) | python-jose (JWT for email approval links), passlib (bcrypt) |
| Email | aiosmtplib 3.0 |
| Scheduler | APScheduler 3.10 |
| HTTP client | httpx 0.27 |
| Frontend | React 18, Vite 5 |
| DB | MySQL 8+ |

---
## SQL Script

> Run this script **after** ensuring the `multimedia_governance` schema and the `user_details` table already exist (they are owned by the client).
> `CREATE INDEX IF NOT EXISTS` requires MySQL 8.0.16+.

```sql
-- =============================================================================
-- WorkflowOS — Database Schema
-- Target schema : multimedia_governance
-- NOTE: `multimedia_governance.user_details` (with primary key `userId`) is
--       assumed to already exist. This script creates all workflow tables only.
-- Run with: mysql -u wfuser -p < schema.sql
-- =============================================================================

SET FOREIGN_KEY_CHECKS = 0;

-- Make sure the schema exists
CREATE SCHEMA IF NOT EXISTS multimedia_governance;

USE multimedia_governance;

-- -----------------------------------------------------------------------------
-- approver_groups
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.approver_groups (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    description TEXT,
    created_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- approver_group_members
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.approver_group_members (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    group_id INT NOT NULL,
    user_id  BIGINT NOT NULL,
    CONSTRAINT fk_agm_group FOREIGN KEY (group_id)
        REFERENCES multimedia_governance.approver_groups (id) ON DELETE CASCADE,
    CONSTRAINT fk_agm_user  FOREIGN KEY (user_id)
        REFERENCES multimedia_governance.user_details (userId) ON DELETE CASCADE,
    UNIQUE KEY uq_group_user (group_id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- workflows
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.workflows (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    name                 VARCHAR(200) NOT NULL,
    description          TEXT,
    type                 ENUM('approval','review','acknowledgement','signature') NOT NULL,
    folder_trigger       VARCHAR(300),
    is_active            TINYINT(1) NOT NULL DEFAULT 1,
    escalation_hours     INT NOT NULL DEFAULT 24,
    rejection_behavior   ENUM('stop','restart','escalate') NOT NULL DEFAULT 'stop',
    notification_channel ENUM('email','slack','both') NOT NULL DEFAULT 'email',
    auto_approve_hours   INT,
    amount_threshold     DOUBLE,
    created_by_id        BIGINT,
    created_at           DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at           DATETIME(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_wf_created_by FOREIGN KEY (created_by_id)
        REFERENCES multimedia_governance.user_details (userId)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- workflow_stages
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.workflow_stages (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    workflow_id       INT NOT NULL,
    name              VARCHAR(200) NOT NULL,
    type              ENUM('approval','review','acknowledgement','signature') NOT NULL,
    `order`           INT NOT NULL,
    approver_group_id INT,
    sla_hours         INT NOT NULL DEFAULT 48,
    voting_rule       ENUM('any','all','sequential') NOT NULL DEFAULT 'any',
    condition_field   VARCHAR(100),
    condition_op      VARCHAR(20),
    condition_value   VARCHAR(300),
    CONSTRAINT fk_ws_workflow FOREIGN KEY (workflow_id)
        REFERENCES multimedia_governance.workflows (id) ON DELETE CASCADE,
    CONSTRAINT fk_ws_group   FOREIGN KEY (approver_group_id)
        REFERENCES multimedia_governance.approver_groups (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- workflow_requests
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.workflow_requests (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    title         VARCHAR(300) NOT NULL,
    description   TEXT,
    document_name VARCHAR(300),
    document_url  VARCHAR(500),
    amount        DOUBLE,
    department    VARCHAR(100),
    request_type  VARCHAR(100),
    workflow_id   INT,
    submitter_id  BIGINT,
    status        ENUM('pending','approved','rejected','escalated','cancelled')
                      NOT NULL DEFAULT 'pending',
    current_stage INT NOT NULL DEFAULT 0,
    submitted_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    resolved_at   DATETIME(6),
    sla_deadline  DATETIME(6),
    CONSTRAINT fk_wr_workflow  FOREIGN KEY (workflow_id)
        REFERENCES multimedia_governance.workflows (id),
    CONSTRAINT fk_wr_submitter FOREIGN KEY (submitter_id)
        REFERENCES multimedia_governance.user_details (userId)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- request_stages
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.request_stages (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    request_id      INT NOT NULL,
    stage_id        INT,
    stage_order     INT NOT NULL,
    status          ENUM('pending','approved','rejected','escalated','cancelled')
                        NOT NULL DEFAULT 'pending',
    started_at      DATETIME(6),
    completed_at    DATETIME(6),
    sla_deadline    DATETIME(6),
    is_sla_breached TINYINT(1) NOT NULL DEFAULT 0,
    CONSTRAINT fk_rs_request FOREIGN KEY (request_id)
        REFERENCES multimedia_governance.workflow_requests (id) ON DELETE CASCADE,
    CONSTRAINT fk_rs_stage   FOREIGN KEY (stage_id)
        REFERENCES multimedia_governance.workflow_stages (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- approval_actions
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.approval_actions (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    request_stage_id INT NOT NULL,
    approver_id      BIGINT,
    decision         ENUM('approved','rejected','delegated') NOT NULL,
    comment          TEXT,
    delegated_to_id  BIGINT,
    acted_at         DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_aa_stage     FOREIGN KEY (request_stage_id)
        REFERENCES multimedia_governance.request_stages (id) ON DELETE CASCADE,
    CONSTRAINT fk_aa_approver  FOREIGN KEY (approver_id)
        REFERENCES multimedia_governance.user_details (userId),
    CONSTRAINT fk_aa_delegated FOREIGN KEY (delegated_to_id)
        REFERENCES multimedia_governance.user_details (userId)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- activity_log
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.activity_log (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    request_id INT,
    user_id    BIGINT,
    action     VARCHAR(100) NOT NULL,
    detail     TEXT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_al_request FOREIGN KEY (request_id)
        REFERENCES multimedia_governance.workflow_requests (id) ON DELETE CASCADE,
    CONSTRAINT fk_al_user    FOREIGN KEY (user_id)
        REFERENCES multimedia_governance.user_details (userId)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- webhook_configs
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS multimedia_governance.webhook_configs (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    workflow_id INT,
    event       VARCHAR(100) NOT NULL,
    url         VARCHAR(500) NOT NULL,
    secret      VARCHAR(200),
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    created_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_wc_workflow FOREIGN KEY (workflow_id)
        REFERENCES multimedia_governance.workflows (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- Useful indexes
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_wr_status    ON multimedia_governance.workflow_requests (status);
CREATE INDEX IF NOT EXISTS idx_wr_submitter ON multimedia_governance.workflow_requests (submitter_id);
CREATE INDEX IF NOT EXISTS idx_wr_workflow  ON multimedia_governance.workflow_requests (workflow_id);
CREATE INDEX IF NOT EXISTS idx_rs_request   ON multimedia_governance.request_stages (request_id);
CREATE INDEX IF NOT EXISTS idx_rs_status    ON multimedia_governance.request_stages (status);
CREATE INDEX IF NOT EXISTS idx_rs_sla       ON multimedia_governance.request_stages (sla_deadline, is_sla_breached);
CREATE INDEX IF NOT EXISTS idx_aa_stage     ON multimedia_governance.approval_actions (request_stage_id);
CREATE INDEX IF NOT EXISTS idx_aa_approver  ON multimedia_governance.approval_actions (approver_id);
CREATE INDEX IF NOT EXISTS idx_al_request   ON multimedia_governance.activity_log (request_id);
CREATE INDEX IF NOT EXISTS idx_al_created   ON multimedia_governance.activity_log (created_at);

SET FOREIGN_KEY_CHECKS = 1;
```