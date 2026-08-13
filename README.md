BudgetControl - Full-Stack Setup
BudgetControl is a FastAPI + React app for managing an annual budget by department, project, WBS activity, monthly phase, approval workflow, and Excel upload.

Stack
Backend: FastAPI + SQLAlchemy
Database: MySQL when configured, otherwise local SQLite
Frontend: React + Vite, implemented mainly in frontend/src/BudgetApp.jsx
Fiscal year: FY 2026-27, Apr-26 through Mar-27
Prerequisites
Python 3.10+
Node 18+
MySQL 8.x, optional
The app runs locally without MySQL. If no database credentials are configured, the backend uses SQLite at backend/budget_app.db.

Database Selection
The backend picks a database in this priority order from backend/database.py:

Priority	Condition	Database used
1	DATABASE_URL is set	The supplied URL
2	DB_HOST, DB_USER, and DB_PASSWORD are set	MySQL via PyMySQL
3	Neither of the above	SQLite at backend/budget_app.db
To use MySQL, create the database first:

CREATE DATABASE budget_app CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
Backend Setup
cd backend
cp .env.example .env
# For MySQL: fill in DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
# For SQLite: leave DB_* values blank or omit them.

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
On startup, init_db() runs automatically. It creates missing tables, applies lightweight column migrations for existing local DB files, and seeds reference data when the database is empty.

Interactive API docs are available at:

http://localhost:8000/docs
Frontend Setup
cd frontend
printf "VITE_API_BASE=http://localhost:8000\n" > .env

npm install
npm run dev
The app runs on:

http://localhost:5173
The frontend reads VITE_API_BASE and calls the backend directly. If the value is not set, it falls back to http://localhost:8000.

Pages
Page	Description
Dashboard	KPI cards, department summaries, procurement pipeline, Opex/Capex totals, lapsing budget table
Budget Book	WBS tree, monthly phasing view, ribbon charts, line drawer, change-request actions
Change Requests	Audit list of raised budget change requests
Approvals	Approve or reject pending change requests and pending budget uploads
Budget Versions	Create versions, set active version, lock or unlock a version
WBS Hierarchy	Department, project, activity, and sub-activity hierarchy editor
Budget Lines	Flat activity and sub-activity table with filters and inline editing
Excel Upload	Upload department-scoped budget spreadsheets for approval
Employees	Employee list and employee creation
Approval Workflows
BudgetControl has two approval-controlled workflows:

Change requests: creating a request only records intent. Budget is moved only when PATCH /api/change-requests/{id}/decide approves it.
Excel budget uploads: uploading a spreadsheet stages validated rows as Pending. Approval marks that department complete for the fiscal-year cycle. Rows are written to live activities only after every project-owning department has an approved, not-yet-merged upload for that same fiscal year.
When the final required department approves, the backend merges all approved staged uploads for that fiscal-year cycle into the live activity table in one transaction. Overall budget views update because they sum the live activity data.

Legacy /api/transfers are immediate and update annual totals directly.

Excel Upload Format
Budget uploads must be scoped to a single department with form field dept_code. The upload endpoint accepts .xlsx or .xls files.

Required columns, case-insensitive and order-flexible:

project_code, activity_name, wbs, allocated
Optional columns:

cost_type, pr, po, invoiced, employee_code, status_code
Rows are skipped if required fields are missing, the project is unknown, or the project belongs to a different department.

API Reference
Method	Path	Description
GET	/api/dashboard	Aggregated KPIs and department summaries
GET	/api/organisations	List organisations
GET/POST	/api/departments	List or create departments
PATCH	/api/departments/{dept_code}/set-head	Assign or clear a department head
GET/POST	/api/projects	List or create projects, filter with ?dept_code=
GET	/api/cost-types	List Opex/Capex lookup values
GET	/api/statuses	List statuses sorted by sort_order
GET/POST	/api/employees	List or create employees
GET/POST	/api/activities	List or create activities, with filters dept_code, project_code, cost_type, status, search
PATCH	/api/activities/{code}	Edit activity totals, status, or owner
DELETE	/api/activities/{code}	Delete an empty activity with no budget/spend, children, or pending requests
GET/POST	/api/sub-activities	List or create sub-activities, filter with ?parent_activity_code=
PATCH	/api/sub-activities/{code}	Edit sub-activity totals, status, or owner
DELETE	/api/sub-activities/{code}	Delete an empty sub-activity with no budget/spend
GET/POST	/api/budget-versions	List or create budget versions
PATCH	/api/budget-versions/{code}/set-active	Mark one budget version active
PATCH	/api/budget-versions/{code}/toggle-lock	Lock or unlock a budget version
GET	/api/fiscal-years	List distinct fiscal years
GET	/api/fiscal-periods	List Apr-Mar fiscal periods
GET/POST	/api/transfers	List or execute legacy immediate transfers
GET/POST	/api/change-requests	List or raise governed change requests
PATCH	/api/change-requests/{id}/decide	Approve or reject a change request
POST	/api/budget-upload	Stage a department-scoped Excel budget upload
GET	/api/budget-uploads	List upload history, filter with ?dept_code= or ?status=
PATCH	/api/budget-uploads/{id}/decide	Approve or reject a staged budget upload
Tests
cd backend
pip install -r requirements.txt -r requirements-dev.txt
pytest -v
Tests use an in-memory SQLite database from backend/tests/conftest.py, so they do not touch the local SQLite file or a configured MySQL database.

CORS
The backend currently allows all origins:

allow_origins=["*"]
For production, restrict this to the deployed frontend origin in backend/main.py.