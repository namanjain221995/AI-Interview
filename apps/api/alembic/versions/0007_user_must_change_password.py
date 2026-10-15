"""Staff accounts with an admin-issued password must choose their own before using the product (S13).

``users.must_change_password`` is set when an administrator creates an account or resets someone's password
and cleared when the user changes it themselves (``POST /auth/password``). While it is set every staff API
call except ``/auth/me``, ``/auth/password`` and ``/auth/logout`` answers 403 ``password_change_required``.
Existing accounts (and seeded development accounts) keep working: the column defaults to false.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
