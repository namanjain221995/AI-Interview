"""Global catalog uniqueness: NULLS NOT DISTINCT + one default template per round type.

Unique constraints over (organization_id, key) did not stop duplicate global rows
(organization_id IS NULL), because NULLs compare distinct by default. PostgreSQL 15+
supports NULLS NOT DISTINCT; the server runs 17.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_CONSTRAINTS = (
    ("competencies", "uq_competencies_org_key", ("organization_id", "key")),
    ("question_bank", "uq_question_bank_org_external_key", ("organization_id", "external_key")),
    ("coding_questions", "uq_coding_questions_org_slug", ("organization_id", "slug")),
)


def upgrade() -> None:
    for table, name, columns in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(name, table, list(columns), postgresql_nulls_not_distinct=True)
    op.create_index(
        "uq_interview_templates_global_default",
        "interview_templates",
        ["round_type"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_interview_templates_global_default", table_name="interview_templates")
    for table, name, columns in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="unique")
        op.create_unique_constraint(name, table, list(columns))
