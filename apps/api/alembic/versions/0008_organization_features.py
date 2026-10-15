"""Per-organisation features (entitlements) managed by the platform, starting with intake (G8).

``organizations.features`` (JSONB, default ``{}``) holds what an organisation's plan includes; only platform
staff change it (``PATCH /organizations/{id}``), never the organisation's own settings. The intake module was
on for every tenant behind one global flag; organisations that already use it keep it (backfilled below: intake
rows, intake settings, intake rounds - even without an invitation yet - or own intake templates), every other one
starts without it. C17 (plans and entitlements) will back this column later.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("features", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.execute(
        """
        UPDATE organizations SET features = features || '{"intake": true}'::jsonb
        WHERE id IN (SELECT organization_id FROM intake_interviews)
           OR id IN (SELECT organization_id FROM intake_org_settings)
           OR id IN (SELECT j.organization_id FROM rounds r JOIN jobs j ON j.id = r.job_id
                     WHERE r.round_type = 'intake')
           OR id IN (SELECT organization_id FROM interview_templates
                     WHERE round_type = 'intake' AND organization_id IS NOT NULL)
        """
    )


def downgrade() -> None:
    op.drop_column("organizations", "features")
