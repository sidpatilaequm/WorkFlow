# WorkflowOS — Document Approval Workflow Engine

Full-stack: **FastAPI + MySQL** backend · **React + Vite** frontend.

> **Auth model:** Most endpoints accept `?user_id=<id>` as a query parameter for identity. A small number of endpoints (`PATCH /api/auth/me/out-of-office`, `POST /api/requests/{id}/send-message`, `GET /api/auth/me`) require a Bearer JWT obtained from `POST /api/auth/login`. See [Auth](#auth) for details.

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
7. [Role-Based Access](#role-based-access)
8. [Workflow Engine Logic](#workflow-engine-logic)
9. [Stage Types & Email Actions](#stage-types--email-actions)
10. [Notifications](#notifications)
11. [Background Jobs (Scheduler)](#background-jobs-scheduler)
12. [Running Tests](#running-tests)
13. [Error Responses](#error-responses)
14. [Tech Stack](#tech-stack)
15. [Changelog](#changelog)

---

## Features

| Module | What's built |
|---|---|
| **Auth** | Register, login, JWT refresh, `/me`, self-service OOO + delegate via `PATCH /api/auth/me/out-of-office` |
| **Workflows** | CRUD, 4 stage types (approval / review / acknowledgement / signature), per-stage custom button labels, success/failure redirect URLs, amount-based auto-approve, message variables (formula-derived template values) |
| **Stages** | Per-stage SLA deadlines, voting rules (any / all / sequential), optional stages (auto-skipped when empty), optional members (vote recorded but excluded from completion math), per-stage instructions |
| **Requests** | Submit with document upload, cancel, track with live stage progress, one-click email approval, ad-hoc `send-message` with repeat reminders, workflow snapshot isolation (in-flight requests follow the config at submission time) |
| **Approvals** | Approve / reject / delegate, document attachment on action, rejection behaviors (stop / restart / escalate), duplicate prevention, OOO delegation (stand-in acts for absent member, action recorded against their slot) |
| **Analytics** | Approval rate, avg resolution time, SLA breach count, by-workflow breakdown, approver performance (with avg response time), activity feed, notification compliance report |
| **Notifications** | Async SMTP email with stage-type-aware action buttons and custom per-stage labels, Slack Block Kit DMs, scheduled ad-hoc message reminders |
| **Scheduler** | APScheduler: SLA escalation every 15 min, snapshot-based pending-approver reminders every hour, scheduled message re-sends |
| **Audit trail** | Every state change (submit, approve, reject, escalate, cancel, skip, reminder sent, message sent) writes an `ActivityLog` row with actor, detail, and stage order |

---

## Project Structure

```
Vendors_Workflow/
└── backend/
    ├── main.py                  # FastAPI app, lifespan, CORS, routers, global error handler
    ├── database.py              # SQLAlchemy engine + session factory
    ├── models.py                # All ORM models (11 tables)
    ├── schemas.py               # Pydantic request/response schemas
    ├── auth_utils.py            # JWT (access + refresh + approval-link tokens), bcrypt, OOO resolver
    ├── template_utils.py        # Sandboxed formula evaluator + {{field}} template renderer
    ├── workflow_snapshot.py     # Freeze/read per-request stage config at submission time
    ├── webhook_utils.py         # Outgoing webhook dispatcher (implemented, not yet wired — see below)
    ├── requirements.txt
    ├── alembic.ini
    ├── .env                     # Environment config (never commit secrets)
    ├── uploads/                 # Uploaded document files (served at /api/uploads/)
    ├── routers/
    │   ├── auth.py              # register, login, refresh, /me, /me/out-of-office
    │   ├── workflows.py         # CRUD /api/workflows
    │   ├── requests.py          # Submit/list/cancel/send-message/one-click  /api/requests
    │   ├── stages.py            # Approver groups + stage management + substitute  /api/stages
    │   ├── approvals.py         # Approve/reject/delegate  /api/approvals
    │   └── analytics.py         # Metrics + notification report  /api/analytics
    └── services/
        ├── notification.py      # Async email (aiosmtplib) + Slack API
        └── escalation.py        # APScheduler: SLA breach + reminder + scheduled message jobs
```

> **Note on `webhook_utils.py`:** The outgoing webhook dispatcher is fully implemented (HMAC-SHA256 signing, event payloads) but is not yet wired into any router or scheduler job, and there is no CRUD endpoint for `WebhookConfig` rows. The feature should be considered **not active**. The README previously described it as working — that was inaccurate.

---

## Database Schema

> All tables — including the client-owned `user_details` table — reside in the **`multimedia_governance`** schema.

```
multimedia_governance.user_details  ← pre-existing client table
  userId (PK), email, firstName, lastName, phoneNumber, designation,
  onboardingStatus, onboardingToken, tokenExpiry, userType,
  signupDate, created_date, modified_date,
  super_admin_id, company_id,
  is_active, ooo_until, delegate_id → user_details.userId  ← OOO delegation

multimedia_governance.approver_groups
  id, name, description, created_at

multimedia_governance.approver_group_members
  id, group_id → approver_groups.id, user_id → user_details.userId,
  sequential_order, is_optional  ← excluded from voting math when true

multimedia_governance.workflows
  id, name, description, type(approval|review|acknowledgement|signature),
  folder_trigger, is_active, escalation_hours,
  rejection_behavior(stop|restart|escalate),
  notification_channel(email|slack|both),
  auto_approve_hours, amount_threshold, auto_approve_conditions (JSON),
  reminder_after_hours, reminder_interval_hours,  ← stage reminder schedule
  message_variables (JSON),                        ← formula-derived template vars
  success_redirect_url, failure_redirect_url,      ← post-action browser redirect
  created_by_id → user_details.userId, created_at, updated_at

multimedia_governance.workflow_stages
  id, workflow_id → workflows.id, name, type, order,
  approver_group_id → approver_groups.id, sla_hours,
  voting_rule(any|all|sequential),
  approve_label, reject_label,  ← custom button text per stage
  is_optional, instructions, condition_field, condition_op, condition_value

multimedia_governance.workflow_requests
  id, title, description, document_name, document_url, document_type,
  folder_path, amount, department, request_type, request_metadata (JSON),
  workflow_id → workflows.id, submitter_id → user_details.userId,
  status(pending|approved|rejected|escalated|cancelled),
  current_stage, submitted_at, resolved_at, sla_deadline,
  workflow_snapshot (JSON)  ← frozen config at submission time

multimedia_governance.request_stages
  id, request_id → workflow_requests.id, stage_id → workflow_stages.id,
  stage_order, status, started_at, completed_at, sla_deadline,
  is_sla_breached, last_reminded_at

multimedia_governance.approval_actions
  id, request_stage_id → request_stages.id,
  approver_id → user_details.userId,
  decision(approved|rejected|delegated), comment,
  document_name, document_url,  ← optional attachment on action
  delegated_to_id → user_details.userId, acted_at

multimedia_governance.activity_log
  id, request_id → workflow_requests.id,
  user_id → user_details.userId (nullable = system),
  action, detail, stage_order, extra (JSON), created_at

multimedia_governance.scheduled_messages
  id, request_id → workflow_requests.id,
  sender_id → user_details.userId, to, custom_emails (JSON),
  subject, message, reminder_interval_hours, max_reminders,
  reminders_sent, last_sent_at, is_active, created_at

multimedia_governance.webhook_configs
  id, workflow_id → workflows.id (nullable = global),
  event, url, secret, is_active, created_at
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

### Auth

Auth endpoints use Bearer JWT for identity. Obtain a token via `POST /api/auth/login`.

#### `POST /api/auth/register`

Create a new user account.

**Request body**
```json
{ "firstName": "Jane", "lastName": "Doe", "email": "jane@co.com", "password": "secret", "role": "approver" }
```
**Response `200`** — `UserOut` object. **Response `400`** — email already registered.

---

#### `POST /api/auth/login`

Authenticate and receive an access token + refresh token.

**Request body**
```json
{ "email": "jane@co.com", "password": "secret" }
```

**Response `200`**
```json
{ "access_token": "<jwt>", "token_type": "bearer", "user": { "id": 3, "email": "jane@co.com", "role": "approver" } }
```

---

#### `POST /api/auth/refresh`

Exchange a refresh token for a new access + refresh token pair.

**Request body:** `{ "refresh_token": "<jwt>" }` · **Response `200`:** `{ "access_token": "...", "refresh_token": "..." }`

---

#### `GET /api/auth/me`

**Header:** `Authorization: Bearer <token>` · **Response `200`:** `UserOut`

---

#### `PATCH /api/auth/me/out-of-office`

**Header:** `Authorization: Bearer <token>`

Set or clear your OOO window and designate a delegate. While `ooo_until` is in the future, stage notification emails are redirected to your delegate, and your delegate may act on your behalf — the action is recorded against your approver slot so voting math stays correct. Pass `ooo_until: null` to clear.

**Request body**
```json
{ "ooo_until": "2026-07-01T00:00:00", "delegate_id": 7 }
```

**Response `200`** — updated `UserOut`. **Response `400`** — cannot delegate to self. **Response `404`** — delegate not found.

---

### Workflows

All workflow endpoints require `?user_id=<id>`.

#### `GET /api/workflows/?user_id=<id>`
List all active workflows with their stages. Any active user.

#### `GET /api/workflows/{wf_id}?user_id=<id>`
Fetch one workflow. **Response `404`** if not found.

#### `POST /api/workflows/?user_id=<id>`
Create a workflow with stages. Requires `role = admin`.

**Request body**
```json
{
  "name": "Vendor Invoice Approval",
  "type": "approval",
  "escalation_hours": 24,
  "rejection_behavior": "stop",
  "notification_channel": "email",
  "amount_threshold": 10000.0,
  "reminder_after_hours": 8,
  "reminder_interval_hours": 4,
  "success_redirect_url": "https://app.example.com/approved",
  "failure_redirect_url": "https://app.example.com/rejected",
  "message_variables": [
    { "name": "tax", "formula": "amount * 0.18" },
    { "name": "total", "formula": "amount + tax" }
  ],
  "stages": [
    {
      "name": "Finance Review",
      "type": "approval",
      "order": 1,
      "approver_group_id": 2,
      "sla_hours": 48,
      "voting_rule": "any",
      "approve_label": "Authorise",
      "reject_label": "Send Back",
      "is_optional": false,
      "instructions": "Please review the invoice total: {{total}}"
    }
  ]
}
```

**`voting_rule`:** `any` · `all` · `sequential` · **`rejection_behavior`:** `stop` · `restart` · `escalate`

**`approve_label` / `reject_label`:** Override the default button text for this stage in email notifications. Falls back to stage-type defaults when null.

**`message_variables`:** Ordered list of `{name, formula}` pairs. Formulas may reference base request fields (`amount`, `title`, `department`, `request_type`, `document_name`, `document_type`) and earlier-defined variables. Arithmetic-only (no function calls). Used in `instructions` template rendering and ad-hoc messages.

**`reminder_after_hours`:** Hours after a stage starts before the first reminder is sent. **`reminder_interval_hours`:** How often (hours) to repeat reminders until the stage resolves.

**`success_redirect_url` / `failure_redirect_url`:** Where the browser should go after a one-click email action resolves. Falls back to `FRONTEND_URL/requests/{id}` when null.

#### `PATCH /api/workflows/{wf_id}?user_id=<id>`
Partially update. If `stages` is included, existing stages are replaced. Requires `role = admin`.

#### `DELETE /api/workflows/{wf_id}?user_id=<id>`
Delete a workflow and all its stages. Requires `role = admin`. **Response `200`:** `{"detail": "Deleted"}`

---

### Stages & Approver Groups

All endpoints require `?user_id=<id>` unless noted. Admin required for write operations.

#### `GET /api/stages/approver-groups?user_id=<id>` — list all groups with members
#### `POST /api/stages/approver-groups?user_id=<id>` — create group. Body: `{"name": "Legal", "member_ids": [4,5]}`
#### `DELETE /api/stages/approver-groups/{group_id}?user_id=<id>` — delete group
#### `POST /api/stages/approver-groups/{group_id}/members?user_id=<id>` — add member. Body: `{"user_id": 6, "sequential_order": 0}`
#### `DELETE /api/stages/approver-groups/{group_id}/members/{user_id}?user_id=<id>` — remove member

#### `PATCH /api/stages/approver-groups/{group_id}/members/{user_id}`

**Header:** `Authorization: Bearer <token>` (admin only)

Toggle `is_optional` on a group membership. An optional member is notified and may still act, but their decision is excluded from both the approval count and the rejection trigger — the stage can complete without them.

**Body:** `{"is_optional": true, "sequential_order": 0}`

#### `POST /api/stages/approver-groups/{group_id}/members/{old_user_id}/substitute?user_id=<id>`

Swap one member for another in a group while preserving their `sequential_order` and `is_optional` flag. Affects **new requests only** — in-flight requests continue using the snapshot frozen at submission. Admin only.

**Body:** `{"new_user_id": 9}` · **Response `400`** — same user, or new user already in group.

#### `GET /api/stages/users?user_id=<id>` — list all active users (admin only)
#### `POST /api/stages/{wf_id}/stages?user_id=<id>` — add a stage to an existing workflow (admin only)
#### `DELETE /api/stages/{stage_id}?user_id=<id>` — delete a stage (admin only)

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

Record an approve, reject, or delegate decision for the current stage.

**Request body**
```json
{
  "request_id": 42,
  "decision": "approved",
  "comment": "Looks good.",
  "document_name": "signed_copy.pdf",
  "document_url": "/uploads/signed_copy.pdf",
  "delegated_to_id": null
}
```

`decision`: `approved` · `rejected` · `delegated` (requires `delegated_to_id`)

`document_name` / `document_url`: Optional attachment stored on the `ApprovalAction` row and noted in the audit trail.

**OOO stand-in:** If the caller is not themselves a stage member but is the designated delegate of a currently-OOO member, they may act on that member’s behalf. The `ApprovalAction` row records the delegate’s own `user_id` as `approver_id`, while the duplicate-check and voting math key off the principal’s slot.

**Stage completion rules**

| Voting rule | Advance when | Reject when |
|---|---|---|
| `any` | ≥ 1 required approval | ≥ 1 required rejection |
| `all` | required approvals == required member count | ≥ 1 required rejection |
| `sequential` | last required action approved | last required action rejected |

> Optional members (`is_optional=true`) may still act; their decisions appear in the audit trail but are excluded from both counts above.

**Rejection behaviors**

| Behavior | Effect |
|---|---|
| `stop` | Request → `rejected`, resolved |
| `restart` | All stages reset, workflow restarts from stage 1 |
| `escalate` | Request → `escalated`, no further transitions |

**Response `200`** — `ApprovalActionOut` with `id`, `approver_id`, `decision`, `comment`, `document_url`, `acted_at`.

---

#### `GET /api/approvals/pending?user_id=<id>`

Return request IDs awaiting the calling user’s decision. Admins see all pending IDs; approvers see only IDs where they are in the active stage’s frozen group config and have not yet acted.

**Response `200`** — `[42, 47, 51]`

---

### Analytics

All analytics endpoints require `?user_id=<id>` (admin only) and accept `?days=N` (1–365, default 30).

#### `GET /api/analytics/summary` — totals: pending, approved, rejected, escalated, approval rate, avg resolution hours, SLA breach count
#### `GET /api/analytics/by-workflow` — per-workflow breakdown with approval rate
#### `GET /api/analytics/approver-performance` — per-approver decision counts (approved / rejected / delegated) and avg response time in hours
#### `GET /api/analytics/activity-feed` — recent audit events (`?limit=N`, max 200)
#### `GET /api/analytics/notification-report` — notification compliance: how many stages were acted on before a reminder was needed (bypassed) vs. required reminders, and how many are still pending after reminders. Filterable by `?workflow_id=`.

---

## Role-Based Access

| Endpoint category | submitter | approver | admin |
|---|---|---|---|
| Register / login / refresh / me | ✓ | ✓ | ✓ |
| Set OOO / delegate | ✓ | ✓ | ✓ |
| List/view workflows | ✓ | ✓ | ✓ |
| Create/update/delete workflows | — | — | ✓ |
| Submit a request | ✓ | ✓ | ✓ |
| View own requests | ✓ | ✓ | ✓ |
| View requests on their workflows | — | ✓ | ✓ |
| View all requests | — | — | ✓ |
| Cancel own request | ✓ | ✓ | ✓ |
| Cancel any request | — | — | ✓ |
| Approve / reject / delegate | — | ✓ | ✓ |
| Act as OOO delegate | — | ✓ | ✓ |
| View pending approvals | ✓ (empty) | ✓ | ✓ |
| Send ad-hoc message | ✓ (own) | ✓ (own wf) | ✓ |
| Approver group / stage CRUD | — | — | ✓ |
| Toggle optional member | — | — | ✓ |
| Substitute member | — | — | ✓ |
| User list | — | — | ✓ |
| Analytics | — | — | ✓ |
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

## Notifications

### Email (`services/notification.py`)

All email sending is async (`aiosmtplib`). In sync FastAPI handlers, notifications are dispatched via `_run_async()` — a daemon thread calling `asyncio.run()`.

- **`notify_approvers()`** — personalised HTML email per approver. Button labels adapt to stage type and use `approve_label`/`reject_label` when set.
- **`notify_submitter_completed()`** — notifies submitter on full approval or rejection, including all comments.
- **`send_email()`** — silently skips if `SMTP_USER` / `SMTP_PASSWORD` are not configured.

**OOO redirect:** Members whose `ooo_until` is in the future and who have a `delegate_id` set receive notifications at the delegate’s email address instead. The approval token in the link still carries the original member’s ID so the action is recorded against their slot.

**Template rendering:** Stage `instructions` are rendered through `template_utils.render_template()` before sending — `{{field}}` placeholders and formula-derived `message_variables` are substituted.

### Slack

Configure `SLACK_BOT_TOKEN`. `notify_slack_approver()` sends a Block Kit DM with action buttons.

### Ad-hoc messages (`POST /api/requests/{id}/send-message`)

Send a one-off or recurring message to `submitter`, `current_approvers`, or `custom` email addresses. Supports `{{field}}` template substitution. When `reminder_interval_hours` is supplied, a `ScheduledMessage` row is created and the scheduler re-sends until `max_reminders` is reached or the request resolves.

### Outgoing webhooks (`webhook_utils.py`)

Fully implemented with HMAC-SHA256 signing but **not yet wired** into any router or scheduler. No `WebhookConfig` CRUD endpoint exists. Do not document this as a working feature until it is connected.

---

## Background Jobs (Scheduler)

Starts automatically with the FastAPI app (via `lifespan`) and stops cleanly on shutdown.

### Job 1 — SLA escalation (every `ESCALATION_CHECK_INTERVAL` minutes, default 15)
1. Finds `RequestStage` rows where `status = pending`, `sla_deadline <= now`, `is_sla_breached = false`.
2. Sets `is_sla_breached = true`, moves parent request to `escalated`.
3. Writes `ActivityLog: escalated`.

### Job 2 — Pending-approver reminders (every hour)
For each pending `RequestStage` where `started_at + reminder_after_hours <= now`:
1. Resolves the member list from the **frozen workflow_snapshot** (not the live group) so membership changes after submission don’t affect who is reminded.
2. Excludes members who have already acted.
3. Sends reminder email and updates `last_reminded_at`.
4. Re-sends every `reminder_interval_hours` thereafter until the stage resolves.

### Job 3 — Scheduled message re-sends (every hour)
For each active `ScheduledMessage` where `last_sent_at + reminder_interval_hours <= now`:
1. Deactivates if the parent request is no longer `pending`.
2. Deactivates if `reminders_sent >= max_reminders`.
3. Otherwise resolves recipients (submitter / current approvers via snapshot / custom emails), renders `{{field}}` placeholders, sends email, increments `reminders_sent`.

---

## Running Tests

The test suite uses an in-memory SQLite database — no external services needed.

```bash
cd backend
pip install -r tests/requirements-test.txt
pytest tests/ -v
```

**Test files**

| File | What’s covered |
|---|---|
| `test_auth.py` | Register, login, refresh, `/me`, OOO endpoint |
| `test_workflows.py` | Workflow CRUD, message variables, button labels, redirect URLs, snapshot isolation for member add/remove |
| `test_stages.py` | Approver group CRUD, add/remove/toggle-optional/substitute members, workflow stages |
| `test_requests.py` | Submit, auto-approve, list/filter, get, cancel, send-message (all `to` targets, reminders, template rendering) |
| `test_approvals.py` | Voting rules, rejection behaviors, duplicate prevention, authorization, OOO stand-in flow, optional members, optional stage auto-skip, document attachment on action, audit trail completeness |
| `test_analytics_and_actions.py` | Analytics endpoints (summary, by-workflow, approver performance with real data, activity feed, notification report), in-app action, email token action |
| `test_escalation.py` | SLA escalation job, snapshot-based reminder job, scheduled message re-send job |
| `test_template_utils.py` | Formula evaluator (sandbox, arithmetic, error cases), variable resolution (base fields, metadata, chained formulas), template rendering |

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
    id               INT AUTO_INCREMENT PRIMARY KEY,
    group_id         INT NOT NULL,
    user_id          BIGINT NOT NULL,
    sequential_order INT NOT NULL DEFAULT 0,
    is_optional      TINYINT(1) NOT NULL DEFAULT 0,
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
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    name                    VARCHAR(200) NOT NULL,
    description             TEXT,
    type                    ENUM('approval','review','acknowledgement','signature') NOT NULL,
    folder_trigger          VARCHAR(300),
    is_active               TINYINT(1) NOT NULL DEFAULT 1,
    escalation_hours        INT NOT NULL DEFAULT 24,
    rejection_behavior      ENUM('stop','restart','escalate') NOT NULL DEFAULT 'stop',
    notification_channel    ENUM('email','slack','both') NOT NULL DEFAULT 'email',
    auto_approve_hours      INT,
    amount_threshold        DOUBLE,
    auto_approve_conditions JSON,
    success_redirect_url    VARCHAR(500),
    failure_redirect_url    VARCHAR(500),
    reminder_after_hours    INT,
    reminder_interval_hours INT,
    message_variables       JSON,
    created_by_id           BIGINT,
    created_at              DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at              DATETIME(6) ON UPDATE CURRENT_TIMESTAMP(6),
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
    approve_label     VARCHAR(50),
    reject_label      VARCHAR(50),
    is_optional       TINYINT(1) NOT NULL DEFAULT 0,
    instructions      TEXT,
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
    document_type VARCHAR(100),
    folder_path   VARCHAR(300),
    amount        DOUBLE,
    department    VARCHAR(100),
    request_type  VARCHAR(100),
    request_metadata      JSON,
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
    sla_deadline     DATETIME(6),
    is_sla_breached  TINYINT(1) NOT NULL DEFAULT 0,
    last_reminded_at DATETIME(6),
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
    document_name    VARCHAR(300),
    document_url     VARCHAR(500),
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
    id          INT AUTO_INCREMENT PRIMARY KEY,
    request_id  INT,
    user_id     BIGINT,
    action      VARCHAR(100) NOT NULL,
    detail      TEXT,
    stage_order INT,
    extra       JSON,
    created_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
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