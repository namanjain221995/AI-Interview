"""Open interview links (docs/design/OPEN_LINKS.md).

Purely additive: ``interview_links`` (one shareable link per job + round
pipeline, registration form), ``link_registrations`` (one row per person per
link, plus ``blocked`` rows) and ``link_registration_rounds`` (one row per
round attempt; each attempt is a normal ``interviews`` row). No change to
existing tables.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _id() -> sa.Column:
    return sa.Column(
        "id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "interview_links",
        _id(),
        *_timestamps(),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("public_token", sa.String(length=64), nullable=False),
        sa.Column("token_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("round_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False),
        sa.Column("progression", sa.String(length=16), server_default="immediate", nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_registrations", sa.Integer(), nullable=True),
        sa.Column("round_window_hours", sa.Integer(), server_default="72", nullable=False),
        sa.Column("require_resume", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("access_code_hash", sa.String(length=255), nullable=True),
        sa.Column("per_ip_hourly_limit", sa.Integer(), server_default="120", nullable=False),
        sa.Column(
            "duplicate_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("""'{"scope": "job", "device": "flag", "network": "flag"}'::jsonb"""),
            nullable=False,
        ),
        sa.Column("form_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "acknowledged_warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_interview_links_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_interview_links_job_id_jobs", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_interview_links_created_by_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name="fk_interview_links_updated_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_links"),
        sa.UniqueConstraint("public_token", name="uq_interview_links_public_token"),
    )
    op.create_index("ix_interview_links_org_created", "interview_links", ["organization_id", "created_at"])
    op.create_index("ix_interview_links_job", "interview_links", ["job_id"])

    op.create_table(
        "link_registrations",
        _id(),
        *_timestamps(),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("link_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("status_reason", sa.String(length=40), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_key", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("phone_key", sa.String(length=20), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("privacy_ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disclosure_hash", sa.String(length=64), nullable=True),
        sa.Column("personal_token_hash", sa.String(length=64), nullable=True),
        sa.Column("personal_token_issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("personal_failed_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("device_hash", sa.String(length=64), nullable=True),
        sa.Column("ip_hash", sa.String(length=64), nullable=True),
        sa.Column("ip_ua_hash", sa.String(length=64), nullable=True),
        sa.Column("resume_sha256", sa.String(length=64), nullable=True),
        sa.Column("flags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("flag_review", sa.String(length=16), server_default="none", nullable=False),
        sa.Column("flag_reviewed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("flag_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flag_review_note", sa.Text(), nullable=True),
        sa.Column("current_round_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signals_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["link_id"],
            ["interview_links.id"],
            name="fk_link_registrations_link_id_interview_links",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["candidates.id"],
            name="fk_link_registrations_candidate_id_candidates",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["flag_reviewed_by_id"],
            ["users.id"],
            name="fk_link_registrations_flag_reviewed_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_link_registrations"),
        sa.UniqueConstraint("personal_token_hash", name="uq_link_registrations_personal_token_hash"),
    )
    op.create_index(
        "uq_link_registrations_link_email",
        "link_registrations",
        ["link_id", "email_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'blocked'"),
    )
    op.create_index(
        "uq_link_registrations_link_phone",
        "link_registrations",
        ["link_id", "phone_key"],
        unique=True,
        postgresql_where=sa.text("phone_key IS NOT NULL AND status <> 'blocked'"),
    )
    op.create_index(
        "ix_link_registrations_job_email", "link_registrations", ["organization_id", "job_id", "email_key"]
    )
    op.create_index(
        "ix_link_registrations_job_phone", "link_registrations", ["organization_id", "job_id", "phone_key"]
    )
    op.create_index("ix_link_registrations_link_created", "link_registrations", ["link_id", "created_at"])
    op.create_index("ix_link_registrations_link_status", "link_registrations", ["link_id", "status"])
    op.create_index("ix_link_registrations_link_device", "link_registrations", ["link_id", "device_hash"])
    op.create_index("ix_link_registrations_link_resume", "link_registrations", ["link_id", "resume_sha256"])
    op.create_index(
        "ix_link_registrations_link_ipua", "link_registrations", ["link_id", "ip_ua_hash", "created_at"]
    )
    op.create_index("ix_link_registrations_candidate", "link_registrations", ["candidate_id"])

    op.create_table(
        "link_registration_rounds",
        _id(),
        *_timestamps(),
        sa.Column("registration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("round_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("round_name", sa.String(length=200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("interview_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unlocked_by", sa.String(length=16), nullable=False),
        sa.Column("unlocked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["registration_id"],
            ["link_registrations.id"],
            name="fk_link_registration_rounds_registration_id_link_registrations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interviews.id"],
            name="fk_link_registration_rounds_interview_id_interviews",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unlocked_by_user_id"],
            ["users.id"],
            name="fk_link_registration_rounds_unlocked_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_link_registration_rounds"),
        sa.UniqueConstraint("interview_id", name="uq_link_registration_rounds_interview_id"),
        sa.UniqueConstraint(
            "registration_id", "sequence", "attempt", name="uq_link_registration_rounds_attempt"
        ),
    )


def downgrade() -> None:
    op.drop_table("link_registration_rounds")
    op.drop_index("ix_link_registrations_candidate", table_name="link_registrations")
    op.drop_index("ix_link_registrations_link_ipua", table_name="link_registrations")
    op.drop_index("ix_link_registrations_link_resume", table_name="link_registrations")
    op.drop_index("ix_link_registrations_link_device", table_name="link_registrations")
    op.drop_index("ix_link_registrations_link_status", table_name="link_registrations")
    op.drop_index("ix_link_registrations_link_created", table_name="link_registrations")
    op.drop_index("ix_link_registrations_job_phone", table_name="link_registrations")
    op.drop_index("ix_link_registrations_job_email", table_name="link_registrations")
    op.drop_index("uq_link_registrations_link_phone", table_name="link_registrations")
    op.drop_index("uq_link_registrations_link_email", table_name="link_registrations")
    op.drop_table("link_registrations")
    op.drop_index("ix_interview_links_job", table_name="interview_links")
    op.drop_index("ix_interview_links_org_created", table_name="interview_links")
    op.drop_table("interview_links")
