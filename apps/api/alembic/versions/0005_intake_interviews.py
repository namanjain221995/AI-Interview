"""Intake Interview tables (docs/design/INTAKE_INTERVIEW.md section 3).

Purely additive: ``intake_interviews`` (one per intake invitation, drafts included), ``intake_org_settings``
(organisation intake policy), ``intake_stage_results`` / ``intake_activity_results`` (live checkpoints),
``intake_evaluations`` / ``intake_evaluator_runs`` / ``intake_competency_scores`` (evaluation) and
``intake_decisions`` (human decisions mirrored to ``review_decisions``). No change to existing tables; the new
round type ``intake`` fits the existing ``String(32)`` ``round_type`` columns.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def _id() -> sa.Column:
    return sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False)


def _org_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organizations.id"],
        name=f"fk_{table}_organization_id_organizations",
        ondelete="CASCADE",
    )


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=f"fk_{table}_{column}_{target}", ondelete=ondelete
    )


def _jsonb(name: str, default: str) -> sa.Column:
    return sa.Column(name, _JSONB, server_default=default, nullable=False)


def upgrade() -> None:
    # ------------------------------------------------------------------ intake_interviews
    op.create_table(
        "intake_interviews",
        _id(),
        *_timestamps(),
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column("candidate_id", _UUID, nullable=False),
        sa.Column("job_id", _UUID, nullable=False),
        sa.Column("round_id", _UUID, nullable=True),
        sa.Column("interview_id", _UUID, nullable=True),
        sa.Column("batch_id", _UUID, nullable=True),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("title", sa.String(length=200), server_default="Intake Interview", nullable=False),
        sa.Column("target_role", sa.String(length=40), nullable=True),
        _jsonb("settings", "{}"),
        sa.Column("onboarding", _JSONB, nullable=True),
        sa.Column("onboarding_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligibility", _JSONB, nullable=True),
        sa.Column("resume_review", _JSONB, nullable=True),
        sa.Column("resume_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_reviewer_id", _UUID, nullable=True),
        sa.Column("created_by_id", _UUID, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _org_fk("intake_interviews"),
        _fk("intake_interviews", "candidate_id", "candidates", "CASCADE"),
        _fk("intake_interviews", "job_id", "jobs", "CASCADE"),
        _fk("intake_interviews", "round_id", "rounds", "SET NULL"),
        _fk("intake_interviews", "interview_id", "interviews", "CASCADE"),
        sa.ForeignKeyConstraint(
            ["assigned_reviewer_id"],
            ["users.id"],
            name="fk_intake_interviews_assigned_reviewer_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_intake_interviews_created_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_intake_interviews"),
    )
    op.create_index(
        "ix_intake_interviews_org_created", "intake_interviews", ["organization_id", "created_at"]
    )
    op.create_index("ix_intake_interviews_org_status", "intake_interviews", ["organization_id", "status"])
    op.create_index("ix_intake_interviews_candidate", "intake_interviews", ["candidate_id"])
    op.create_index(
        "uq_intake_interviews_interview",
        "intake_interviews",
        ["interview_id"],
        unique=True,
        postgresql_where=sa.text("interview_id IS NOT NULL"),
    )
    op.create_index(
        "ix_intake_interviews_batch",
        "intake_interviews",
        ["batch_id"],
        postgresql_where=sa.text("batch_id IS NOT NULL"),
    )
    op.create_index(
        "ix_intake_interviews_reviewer",
        "intake_interviews",
        ["assigned_reviewer_id"],
        postgresql_where=sa.text("assigned_reviewer_id IS NOT NULL"),
    )

    # ---------------------------------------------------------------- intake_org_settings
    op.create_table(
        "intake_org_settings",
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column(
            "program_name", sa.String(length=200), server_default="AI/ML Training Program", nullable=False
        ),
        sa.Column(
            "work_authorization",
            _JSONB,
            server_default=sa.text(
                """'{"enabled": false, "country": null, "ask_sponsorship": true, "variant": "default"}'::jsonb"""
            ),
            nullable=False,
        ),
        _jsonb("approved_info", "[]"),
        _jsonb("defaults", "{}"),
        sa.Column("updated_by_id", _UUID, nullable=True),
        *_timestamps(),
        _org_fk("intake_org_settings"),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name="fk_intake_org_settings_updated_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("organization_id", name="pk_intake_org_settings"),
    )

    # --------------------------------------------------------------- intake_stage_results
    op.create_table(
        "intake_stage_results",
        _id(),
        *_timestamps(),
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column("intake_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("stage_key", sa.String(length=16), nullable=False),
        sa.Column("stage_number", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("started_elapsed_s", sa.Float(), nullable=True),
        sa.Column("ended_elapsed_s", sa.Float(), nullable=True),
        sa.Column("budget_s", sa.Float(), nullable=True),
        _jsonb("items", "[]"),
        _jsonb("extra", "{}"),
        _org_fk("intake_stage_results"),
        _fk("intake_stage_results", "intake_id", "intake_interviews", "CASCADE"),
        _fk("intake_stage_results", "session_id", "interview_sessions", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_intake_stage_results"),
        sa.UniqueConstraint("session_id", "stage_key", name="uq_intake_stage_results_session_stage"),
    )
    op.create_index("ix_intake_stage_results_intake", "intake_stage_results", ["intake_id"])

    # ------------------------------------------------------------ intake_activity_results
    op.create_table(
        "intake_activity_results",
        _id(),
        *_timestamps(),
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column("intake_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("stage_key", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempt", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("material_key", sa.String(length=8), nullable=False),
        sa.Column("kit_version", sa.String(length=32), nullable=False),
        sa.Column("question_id", _UUID, nullable=True),
        sa.Column("answer_id", _UUID, nullable=True),
        _jsonb("phases", "[]"),
        _jsonb("reveal", "{}"),
        _jsonb("capture", "{}"),
        _jsonb("attempts_log", "[]"),
        sa.Column("transcript_live", sa.Text(), nullable=True),
        sa.Column("transcript_final", sa.Text(), nullable=True),
        sa.Column("transcript_status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("transcript_source", sa.String(length=24), nullable=True),
        sa.Column("audio_media_id", _UUID, nullable=True),
        sa.Column("ended_reason", sa.String(length=24), nullable=True),
        _org_fk("intake_activity_results"),
        _fk("intake_activity_results", "intake_id", "intake_interviews", "CASCADE"),
        _fk("intake_activity_results", "session_id", "interview_sessions", "CASCADE"),
        _fk("intake_activity_results", "question_id", "questions", "SET NULL"),
        _fk("intake_activity_results", "answer_id", "answers", "SET NULL"),
        _fk("intake_activity_results", "audio_media_id", "media_assets", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_intake_activity_results"),
        sa.UniqueConstraint("session_id", "kind", name="uq_intake_activity_results_session_kind"),
    )
    op.create_index("ix_intake_activity_results_intake", "intake_activity_results", ["intake_id"])

    # ----------------------------------------------------------------- intake_evaluations
    op.create_table(
        "intake_evaluations",
        _id(),
        *_timestamps(),
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column("intake_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("requested_by_id", _UUID, nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("input_digest", sa.String(length=64), nullable=True),
        sa.Column("kit_version", sa.String(length=32), nullable=False),
        sa.Column("scoring_version", sa.String(length=32), nullable=False),
        sa.Column("weighted_total", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("partial_total", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("normalized_100", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("band", sa.String(length=24), nullable=True),
        sa.Column("evidence_sufficiency", sa.String(length=16), nullable=True),
        _jsonb("summary", "{}"),
        _jsonb("validator", "{}"),
        _jsonb("review_items", "[]"),
        sa.Column("training_plan", _JSONB, nullable=True),
        _jsonb("model_call_ids", "[]"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _org_fk("intake_evaluations"),
        _fk("intake_evaluations", "intake_id", "intake_interviews", "CASCADE"),
        _fk("intake_evaluations", "session_id", "interview_sessions", "CASCADE"),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name="fk_intake_evaluations_requested_by_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_intake_evaluations"),
        sa.UniqueConstraint("session_id", "version", name="uq_intake_evaluations_session_version"),
    )
    op.create_index("ix_intake_evaluations_intake", "intake_evaluations", ["intake_id", "version"])
    op.create_index(
        "ix_intake_evaluations_org_created", "intake_evaluations", ["organization_id", "created_at"]
    )

    # -------------------------------------------------------------- intake_evaluator_runs
    op.create_table(
        "intake_evaluator_runs",
        _id(),
        *_timestamps(),
        sa.Column("evaluation_id", _UUID, nullable=False),
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("evaluator", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=True),
        sa.Column("output", _JSONB, nullable=True),
        sa.Column("model_call_id", _UUID, nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _fk("intake_evaluator_runs", "evaluation_id", "intake_evaluations", "CASCADE"),
        _org_fk("intake_evaluator_runs"),
        sa.PrimaryKeyConstraint("id", name="pk_intake_evaluator_runs"),
        sa.UniqueConstraint(
            "evaluation_id", "evaluator", name="uq_intake_evaluator_runs_evaluation_evaluator"
        ),
    )
    op.create_index("ix_intake_evaluator_runs_session", "intake_evaluator_runs", ["session_id"])

    # ----------------------------------------------------------- intake_competency_scores
    op.create_table(
        "intake_competency_scores",
        _id(),
        *_timestamps(),
        sa.Column("evaluation_id", _UUID, nullable=False),
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("competency_key", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Numeric(precision=3, scale=1), nullable=True),
        sa.Column("weight", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("weighted", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("evaluator", sa.String(length=32), nullable=False),
        sa.Column("reasoning", sa.Text(), server_default="", nullable=False),
        _jsonb("evidence", "[]"),
        _jsonb("missing_evidence", "[]"),
        _jsonb("strengths", "[]"),
        _jsonb("improvements", "[]"),
        _jsonb("sub_scores", "{}"),
        _fk("intake_competency_scores", "evaluation_id", "intake_evaluations", "CASCADE"),
        _org_fk("intake_competency_scores"),
        sa.PrimaryKeyConstraint("id", name="pk_intake_competency_scores"),
        sa.UniqueConstraint(
            "evaluation_id", "competency_key", name="uq_intake_competency_scores_evaluation_key"
        ),
    )
    op.create_index(
        "ix_intake_competency_scores_org_key",
        "intake_competency_scores",
        ["organization_id", "competency_key"],
    )
    op.create_index("ix_intake_competency_scores_session", "intake_competency_scores", ["session_id"])

    # ------------------------------------------------------------------- intake_decisions
    op.create_table(
        "intake_decisions",
        _id(),
        *_timestamps(),
        sa.Column("organization_id", _UUID, nullable=False),
        sa.Column("intake_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("evaluation_id", _UUID, nullable=True),
        sa.Column("review_decision_id", _UUID, nullable=False),
        sa.Column("reviewer_id", _UUID, nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("ai_band_at_decision", sa.String(length=24), nullable=True),
        sa.Column("ai_total_at_decision", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("overrides_ai", sa.Boolean(), server_default="false", nullable=False),
        _jsonb("review_items_resolution", "[]"),
        _jsonb("competency_notes", "{}"),
        _jsonb("requested_evidence", "[]"),
        _org_fk("intake_decisions"),
        _fk("intake_decisions", "intake_id", "intake_interviews", "CASCADE"),
        _fk("intake_decisions", "session_id", "interview_sessions", "CASCADE"),
        _fk("intake_decisions", "evaluation_id", "intake_evaluations", "SET NULL"),
        _fk("intake_decisions", "review_decision_id", "review_decisions", "CASCADE"),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name="fk_intake_decisions_reviewer_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_intake_decisions"),
        sa.UniqueConstraint("review_decision_id", name="uq_intake_decisions_review_decision_id"),
    )
    op.create_index("ix_intake_decisions_intake_created", "intake_decisions", ["intake_id", "created_at"])
    op.create_index("ix_intake_decisions_org_outcome", "intake_decisions", ["organization_id", "outcome"])


def downgrade() -> None:
    op.drop_index("ix_intake_decisions_org_outcome", table_name="intake_decisions")
    op.drop_index("ix_intake_decisions_intake_created", table_name="intake_decisions")
    op.drop_table("intake_decisions")
    op.drop_index("ix_intake_competency_scores_session", table_name="intake_competency_scores")
    op.drop_index("ix_intake_competency_scores_org_key", table_name="intake_competency_scores")
    op.drop_table("intake_competency_scores")
    op.drop_index("ix_intake_evaluator_runs_session", table_name="intake_evaluator_runs")
    op.drop_table("intake_evaluator_runs")
    op.drop_index("ix_intake_evaluations_org_created", table_name="intake_evaluations")
    op.drop_index("ix_intake_evaluations_intake", table_name="intake_evaluations")
    op.drop_table("intake_evaluations")
    op.drop_index("ix_intake_activity_results_intake", table_name="intake_activity_results")
    op.drop_table("intake_activity_results")
    op.drop_index("ix_intake_stage_results_intake", table_name="intake_stage_results")
    op.drop_table("intake_stage_results")
    op.drop_table("intake_org_settings")
    op.drop_index("ix_intake_interviews_reviewer", table_name="intake_interviews")
    op.drop_index("ix_intake_interviews_batch", table_name="intake_interviews")
    op.drop_index("uq_intake_interviews_interview", table_name="intake_interviews")
    op.drop_index("ix_intake_interviews_candidate", table_name="intake_interviews")
    op.drop_index("ix_intake_interviews_org_status", table_name="intake_interviews")
    op.drop_index("ix_intake_interviews_org_created", table_name="intake_interviews")
    op.drop_table("intake_interviews")
