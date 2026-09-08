"""drop legacy organisation table, add department<->company many-to-many

Revision ID: 0008_drop_organisation_add_department_company
Revises: 0007_email_template_revisions
Create Date: 2026-09-08

`organisation` was a single fake seed row ("ORG-001" / "Aequm India") that department.org_code
pointed at — unrelated to the real company master data that already exists in the `company`
table (backend_java's Company entity: 1000 Ankit Aerospace, 2000 Ankit Fasteners). A department
isn't owned by exactly one company either — it can be assigned to zero, one, or several — so
this replaces the org_code FK with a proper many-to-many join table instead of just repointing
it. department is wiped entirely — departments are created and assigned to companies through
the UI from here on — along with anything still pointing at it (activity/project rows, and
employee/user_details dept_code links).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008_drop_organisation_add_department_company"
down_revision: Union[str, None] = "0007_email_template_revisions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "multimedia_governance"


def upgrade() -> None:
    # department is being wiped entirely, so anything that still points at it needs
    # clearing first: activity/project require their FK (NOT NULL, no cascade), so they're
    # deleted outright; employee/user_details.dept_code are nullable, so those are just
    # unlinked rather than deleting the employee/user rows themselves.
    op.execute(f"DELETE FROM {SCHEMA}.activity")
    op.execute(f"DELETE FROM {SCHEMA}.project")
    op.execute(f"UPDATE {SCHEMA}.employee SET dept_code = NULL")
    op.execute(f"UPDATE {SCHEMA}.user_details SET dept_code = NULL")
    op.execute(f"DELETE FROM {SCHEMA}.department")
    op.execute(f"ALTER TABLE {SCHEMA}.department DROP FOREIGN KEY department_ibfk_1")
    op.execute(f"ALTER TABLE {SCHEMA}.department DROP COLUMN org_code")
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.organisation")
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.department_company (
            dept_code    VARCHAR(20) NOT NULL,
            company_code VARCHAR(4)  NOT NULL,
            PRIMARY KEY (dept_code, company_code),
            CONSTRAINT fk_dept_company_dept
                FOREIGN KEY (dept_code) REFERENCES {SCHEMA}.department (dept_code)
                ON DELETE CASCADE,
            CONSTRAINT fk_dept_company_company
                FOREIGN KEY (company_code) REFERENCES {SCHEMA}.company (company_code)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def downgrade() -> None:
    # Schema-shape reversal only — the wiped department rows and their org_code values are
    # not recoverable from this migration.
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.department_company")
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS {SCHEMA}.organisation (
            org_code      VARCHAR(20)  NOT NULL,
            name          VARCHAR(120) NOT NULL,
            base_currency VARCHAR(3)   NOT NULL DEFAULT 'INR',
            fiscal_year   VARCHAR(20)  NOT NULL,
            PRIMARY KEY (org_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    op.execute(f"""
        INSERT INTO {SCHEMA}.organisation (org_code, name, base_currency, fiscal_year)
        VALUES ('ORG-001', 'Aequm India', 'INR', 'FY 2026-27')
    """)
    op.execute(f"ALTER TABLE {SCHEMA}.department ADD COLUMN org_code VARCHAR(20) NOT NULL DEFAULT 'ORG-001'")
    op.execute(f"""
        ALTER TABLE {SCHEMA}.department
            ADD CONSTRAINT department_ibfk_1
            FOREIGN KEY (org_code) REFERENCES {SCHEMA}.organisation (org_code)
    """)
