"""Scores without evidence are NULL, never a made-up midpoint.

``scores.score`` becomes nullable: a competency nothing was observed for, a rubric category with nothing
assessed, or an overall score of a session where nothing was assessed (silent / too-short interview) is
stored as NULL with ``sufficient_evidence = false``. Relaxing NOT NULL is backwards compatible for writers;
readers of the new code handle NULL.

Downgrade restores NOT NULL by deleting the NULL rows (they carry no score; the session's scores can be
recomputed by the old code afterwards).

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("scores", "score", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM scores WHERE score IS NULL")
    op.alter_column("scores", "score", existing_type=sa.Float(), nullable=False)
