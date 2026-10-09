"""Initial schema: all 37 platform tables.

Generated with ``alembic revision --autogenerate`` from ``app.db.models`` and
reviewed by hand:

* the ``vector`` (pgvector), ``pg_trgm`` and ``pgcrypto`` extensions are
  created first (``gen_random_uuid()`` defaults and ``VECTOR(1024)`` columns
  depend on them; ``infra/postgres/init.sql`` creates them too, hence
  ``IF NOT EXISTS``);
* HNSW cosine indexes on ``question_bank.embedding`` and
  ``resume_chunks.embedding`` (m=16, ef_construction=64);
* server defaults are kept exactly as declared on the models;
* ``downgrade`` drops every table/index in dependency order. It deliberately
  leaves the extensions installed: they may predate this migration and other
  databases objects may use them.

Revision ID: 0001
Revises:
Create Date: 2026-10-07 03:30:39.653546+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0001'
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EXTENSIONS = ("vector", "pg_trgm", "pgcrypto")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(sa.text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('resource_type', sa.String(length=64), nullable=True),
    sa.Column('resource_id', sa.String(length=64), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index('ix_audit_logs_org_created', 'audit_logs', ['organization_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_organization_id'), 'audit_logs', ['organization_id'], unique=False)
    op.create_index('ix_audit_logs_resource', 'audit_logs', ['resource_type', 'resource_id'], unique=False)
    op.create_table('background_jobs',
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('dedupe_key', sa.String(length=200), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_background_jobs')),
    sa.UniqueConstraint('dedupe_key', name=op.f('uq_background_jobs_dedupe_key'))
    )
    op.create_index('ix_background_jobs_status_type', 'background_jobs', ['status', 'type'], unique=False)
    op.create_table('model_calls',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('task', sa.String(length=48), nullable=False),
    sa.Column('endpoint', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('latency_ms', sa.Float(), nullable=True),
    sa.Column('ttft_ms', sa.Float(), nullable=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('completion_tokens', sa.Integer(), nullable=True),
    sa.Column('repaired', sa.Boolean(), nullable=False),
    sa.Column('used_fallback', sa.Boolean(), nullable=False),
    sa.Column('prompt_version', sa.String(length=40), nullable=True),
    sa.Column('request_hash', sa.String(length=64), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('request_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('response_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_model_calls'))
    )
    op.create_index(op.f('ix_model_calls_session_id'), 'model_calls', ['session_id'], unique=False)
    op.create_index('ix_model_calls_task_created', 'model_calls', ['task', 'created_at'], unique=False)
    op.create_table('organizations',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=100), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_organizations')),
    sa.UniqueConstraint('slug', name=op.f('uq_organizations_slug'))
    )
    op.create_table('system_notes',
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_system_notes'))
    )
    op.create_table('coding_questions',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('difficulty', sa.String(length=16), nullable=False),
    sa.Column('competency_keys', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('role_tags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('prompt_markdown', sa.Text(), nullable=False),
    sa.Column('languages', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('signature', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('starter_code', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('reference_solutions', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('tests', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('sql_setup', sa.Text(), nullable=True),
    sa.Column('expected_concepts', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('optimal_complexity', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('rubric', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('hints', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('followups', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('common_errors', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('time_limit_minutes', sa.Integer(), server_default='20', nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_coding_questions_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_coding_questions')),
    sa.UniqueConstraint('organization_id', 'slug', name='uq_coding_questions_org_slug')
    )
    op.create_index(op.f('ix_coding_questions_organization_id'), 'coding_questions', ['organization_id'], unique=False)
    op.create_table('competencies',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('rubric', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_competencies_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_competencies')),
    sa.UniqueConstraint('organization_id', 'key', name='uq_competencies_org_key')
    )
    op.create_index(op.f('ix_competencies_organization_id'), 'competencies', ['organization_id'], unique=False)
    op.create_table('users',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_login_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_users_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_index(op.f('ix_users_organization_id'), 'users', ['organization_id'], unique=False)
    op.create_table('candidates',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('location', sa.String(length=120), nullable=True),
    sa.Column('source', sa.String(length=80), nullable=True),
    sa.Column('external_ref', sa.String(length=120), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_candidates_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_candidates_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidates')),
    sa.UniqueConstraint('organization_id', 'email', name='uq_candidates_org_email')
    )
    op.create_index(op.f('ix_candidates_organization_id'), 'candidates', ['organization_id'], unique=False)
    op.create_table('interview_templates',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('round_type', sa.String(length=32), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_interview_templates_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_interview_templates_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_templates'))
    )
    op.create_index(op.f('ix_interview_templates_organization_id'), 'interview_templates', ['organization_id'], unique=False)
    op.create_table('jobs',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('department', sa.String(length=120), nullable=True),
    sa.Column('location', sa.String(length=120), nullable=True),
    sa.Column('employment_type', sa.String(length=40), nullable=True),
    sa.Column('seniority', sa.String(length=40), nullable=True),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('experience_min_years', sa.Float(), nullable=True),
    sa.Column('experience_max_years', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='draft', nullable=False),
    sa.Column('interview_instructions', sa.Text(), nullable=True),
    sa.Column('coding_requirements', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('integrity_settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('recording_settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('candidate_report_mode', sa.String(length=16), server_default='after_review', nullable=False),
    sa.Column('allow_candidate_round_selection', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_jobs_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_jobs_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index('ix_jobs_org_status', 'jobs', ['organization_id', 'status'], unique=False)
    op.create_index(op.f('ix_jobs_organization_id'), 'jobs', ['organization_id'], unique=False)
    op.create_table('question_bank',
    sa.Column('organization_id', sa.UUID(), nullable=True),
    sa.Column('external_key', sa.String(length=120), nullable=True),
    sa.Column('role_tags', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('round_types', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('competency_key', sa.String(length=80), nullable=False),
    sa.Column('topic', sa.String(length=120), nullable=True),
    sa.Column('difficulty', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('expected_concepts', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('rubric', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('followups', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('common_errors', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('usage_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_by_id', sa.UUID(), nullable=True),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_question_bank_created_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_question_bank_organization_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_question_bank')),
    sa.UniqueConstraint('organization_id', 'external_key', name='uq_question_bank_org_external_key')
    )
    op.create_index('ix_question_bank_competency_difficulty', 'question_bank', ['competency_key', 'difficulty'], unique=False)
    op.create_index(op.f('ix_question_bank_competency_key'), 'question_bank', ['competency_key'], unique=False)
    op.create_index('ix_question_bank_embedding_hnsw', 'question_bank', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index(op.f('ix_question_bank_organization_id'), 'question_bank', ['organization_id'], unique=False)
    op.create_table('job_requirements',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('competency_key', sa.String(length=80), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('weight', sa.Float(), server_default='1.0', nullable=False),
    sa.Column('min_years', sa.Float(), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_job_requirements_job_id_jobs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_job_requirements'))
    )
    op.create_index(op.f('ix_job_requirements_job_id'), 'job_requirements', ['job_id'], unique=False)
    op.create_table('rounds',
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('template_id', sa.UUID(), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('round_type', sa.String(length=32), nullable=False),
    sa.Column('sequence', sa.Integer(), server_default='1', nullable=False),
    sa.Column('duration_minutes', sa.Integer(), server_default='30', nullable=False),
    sa.Column('max_duration_minutes', sa.Integer(), server_default='40', nullable=False),
    sa.Column('expected_difficulty', sa.String(length=16), server_default='medium', nullable=False),
    sa.Column('candidate_selectable', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('instructions', sa.Text(), nullable=True),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_rounds_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['template_id'], ['interview_templates.id'], name=op.f('fk_rounds_template_id_interview_templates'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rounds'))
    )
    op.create_index(op.f('ix_rounds_job_id'), 'rounds', ['job_id'], unique=False)
    op.create_index('ix_rounds_job_sequence', 'rounds', ['job_id', 'sequence'], unique=False)
    op.create_table('interviews',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('round_id', sa.UUID(), nullable=True),
    sa.Column('allow_round_selection', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='invited', nullable=False),
    sa.Column('invite_token_hash', sa.String(length=64), nullable=False),
    sa.Column('invite_expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('access_code_hash', sa.String(length=255), nullable=True),
    sa.Column('invite_opened_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_verification_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('policy_snapshot', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('system_check', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('invited_by_id', sa.UUID(), nullable=True),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_interviews_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], name=op.f('fk_interviews_invited_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_interviews_job_id_jobs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], name=op.f('fk_interviews_organization_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], name=op.f('fk_interviews_round_id_rounds'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interviews')),
    sa.UniqueConstraint('invite_token_hash', name=op.f('uq_interviews_invite_token_hash'))
    )
    op.create_index(op.f('ix_interviews_candidate_id'), 'interviews', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_interviews_job_id'), 'interviews', ['job_id'], unique=False)
    op.create_index('ix_interviews_org_status', 'interviews', ['organization_id', 'status'], unique=False)
    op.create_index(op.f('ix_interviews_organization_id'), 'interviews', ['organization_id'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('subject_type', sa.String(length=16), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('candidate_id', sa.UUID(), nullable=True),
    sa.Column('interview_id', sa.UUID(), nullable=True),
    sa.Column('family_id', sa.UUID(), nullable=False),
    sa.Column('refresh_token_hash', sa.String(length=64), nullable=False),
    sa.Column('rotated_from_id', sa.UUID(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=64), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_auth_sessions_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], name=op.f('fk_auth_sessions_interview_id_interviews'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('refresh_token_hash', name=op.f('uq_auth_sessions_refresh_token_hash'))
    )
    op.create_index(op.f('ix_auth_sessions_candidate_id'), 'auth_sessions', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_auth_sessions_family_id'), 'auth_sessions', ['family_id'], unique=False)
    op.create_index(op.f('ix_auth_sessions_interview_id'), 'auth_sessions', ['interview_id'], unique=False)
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('candidate_resumes',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('interview_id', sa.UUID(), nullable=True),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='uploaded', nullable=False),
    sa.Column('status_detail', sa.String(length=255), nullable=True),
    sa.Column('progress', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('extraction_method', sa.String(length=16), nullable=True),
    sa.Column('extracted_text', sa.Text(), nullable=True),
    sa.Column('normalized_text', sa.Text(), nullable=True),
    sa.Column('scan_result', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('uploaded_by_candidate', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processing_finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_candidate_resumes_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], name=op.f('fk_candidate_resumes_interview_id_interviews'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_resumes'))
    )
    op.create_index(op.f('ix_candidate_resumes_candidate_id'), 'candidate_resumes', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_candidate_resumes_interview_id'), 'candidate_resumes', ['interview_id'], unique=False)
    op.create_index(op.f('ix_candidate_resumes_organization_id'), 'candidate_resumes', ['organization_id'], unique=False)
    op.create_index(op.f('ix_candidate_resumes_sha256'), 'candidate_resumes', ['sha256'], unique=False)
    op.create_table('consents',
    sa.Column('interview_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('policy_version', sa.String(length=32), nullable=False),
    sa.Column('policy_hash', sa.String(length=64), nullable=False),
    sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], name=op.f('fk_consents_interview_id_interviews'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consents'))
    )
    op.create_index(op.f('ix_consents_candidate_id'), 'consents', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_consents_interview_id'), 'consents', ['interview_id'], unique=False)
    op.create_table('candidate_profiles',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('resume_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='ready', nullable=False),
    sa.Column('profile', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('experience_years', sa.Float(), nullable=True),
    sa.Column('relevant_experience_years', sa.Float(), nullable=True),
    sa.Column('job_match_score', sa.Float(), nullable=True),
    sa.Column('claims_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('model_call_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_candidate_profiles_candidate_id_candidates'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_candidate_profiles_job_id_jobs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['resume_id'], ['candidate_resumes.id'], name=op.f('fk_candidate_profiles_resume_id_candidate_resumes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidate_profiles'))
    )
    op.create_index(op.f('ix_candidate_profiles_candidate_id'), 'candidate_profiles', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_candidate_profiles_resume_id'), 'candidate_profiles', ['resume_id'], unique=False)
    op.create_table('resume_chunks',
    sa.Column('resume_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('section', sa.String(length=32), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['resume_id'], ['candidate_resumes.id'], name=op.f('fk_resume_chunks_resume_id_candidate_resumes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_resume_chunks'))
    )
    op.create_index(op.f('ix_resume_chunks_candidate_id'), 'resume_chunks', ['candidate_id'], unique=False)
    op.create_index('ix_resume_chunks_embedding_hnsw', 'resume_chunks', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index(op.f('ix_resume_chunks_resume_id'), 'resume_chunks', ['resume_id'], unique=False)
    op.create_table('interview_sessions',
    sa.Column('interview_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('job_id', sa.UUID(), nullable=False),
    sa.Column('round_id', sa.UUID(), nullable=False),
    sa.Column('profile_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='created', nullable=False),
    sa.Column('round_config', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('plan', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('state_version', sa.Integer(), server_default='0', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end_reason', sa.String(length=32), nullable=True),
    sa.Column('elapsed_seconds', sa.Float(), server_default='0', nullable=False),
    sa.Column('reconnect_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('client_info', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['interview_id'], ['interviews.id'], name=op.f('fk_interview_sessions_interview_id_interviews'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['candidate_profiles.id'], name=op.f('fk_interview_sessions_profile_id_candidate_profiles'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], name=op.f('fk_interview_sessions_round_id_rounds'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_sessions'))
    )
    op.create_index(op.f('ix_interview_sessions_candidate_id'), 'interview_sessions', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_interview_sessions_interview_id'), 'interview_sessions', ['interview_id'], unique=False)
    op.create_index(op.f('ix_interview_sessions_job_id'), 'interview_sessions', ['job_id'], unique=False)
    op.create_index('ix_interview_sessions_org_status', 'interview_sessions', ['organization_id', 'status'], unique=False)
    op.create_index(op.f('ix_interview_sessions_organization_id'), 'interview_sessions', ['organization_id'], unique=False)
    op.create_table('interview_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('elapsed_seconds', sa.Float(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_interview_events_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_interview_events')),
    sa.UniqueConstraint('session_id', 'seq', name='uq_interview_events_session_seq')
    )
    op.create_index('ix_interview_events_session_type', 'interview_events', ['session_id', 'type'], unique=False)
    op.create_table('media_assets',
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=True),
    sa.Column('candidate_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('content_type', sa.String(length=120), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('retention_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_media_assets_session_id_interview_sessions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_media_assets')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_media_assets_storage_key'))
    )
    op.create_index(op.f('ix_media_assets_candidate_id'), 'media_assets', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_media_assets_organization_id'), 'media_assets', ['organization_id'], unique=False)
    op.create_index(op.f('ix_media_assets_retention_until'), 'media_assets', ['retention_until'], unique=False)
    op.create_index(op.f('ix_media_assets_session_id'), 'media_assets', ['session_id'], unique=False)
    op.create_table('questions',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('parent_question_id', sa.UUID(), nullable=True),
    sa.Column('bank_question_id', sa.UUID(), nullable=True),
    sa.Column('coding_question_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('segment', sa.String(length=32), nullable=True),
    sa.Column('topic', sa.String(length=120), nullable=True),
    sa.Column('competency_keys', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('difficulty', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('spoken_text', sa.Text(), nullable=True),
    sa.Column('question_focus', sa.String(length=300), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('expected_evidence', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('claim_id', sa.String(length=64), nullable=True),
    sa.Column('decision', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('interrupted', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('asked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('elapsed_seconds', sa.Float(), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['bank_question_id'], ['question_bank.id'], name=op.f('fk_questions_bank_question_id_question_bank'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['coding_question_id'], ['coding_questions.id'], name=op.f('fk_questions_coding_question_id_coding_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_question_id'], ['questions.id'], name=op.f('fk_questions_parent_question_id_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_questions_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_questions')),
    sa.UniqueConstraint('session_id', 'sequence', name='uq_questions_session_sequence')
    )
    op.create_index(op.f('ix_questions_session_id'), 'questions', ['session_id'], unique=False)
    op.create_table('reports',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('recommendation', sa.String(length=32), nullable=True),
    sa.Column('overall_score', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('artifact_storage_key', sa.String(length=512), nullable=True),
    sa.Column('model_call_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_to_candidate', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_by_id', sa.UUID(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['released_by_id'], ['users.id'], name=op.f('fk_reports_released_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_reports_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reports')),
    sa.UniqueConstraint('session_id', 'type', 'version', name='uq_reports_session_type_version')
    )
    op.create_index(op.f('ix_reports_session_id'), 'reports', ['session_id'], unique=False)
    op.create_table('review_decisions',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('reviewer_id', sa.UUID(), nullable=False),
    sa.Column('decision', sa.String(length=32), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('ai_recommendation_at_review', sa.String(length=32), nullable=True),
    sa.Column('overrides_ai', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], name=op.f('fk_review_decisions_reviewer_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_review_decisions_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_decisions'))
    )
    op.create_index(op.f('ix_review_decisions_session_id'), 'review_decisions', ['session_id'], unique=False)
    op.create_table('score_snapshots',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('elapsed_seconds', sa.Float(), nullable=False),
    sa.Column('competency_key', sa.String(length=80), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('evidence_count', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(length=32), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_score_snapshots_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_score_snapshots'))
    )
    op.create_index('ix_score_snapshots_session_elapsed', 'score_snapshots', ['session_id', 'elapsed_seconds'], unique=False)
    op.create_table('scores',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('weight', sa.Float(), nullable=True),
    sa.Column('evidence_count', sa.Integer(), nullable=False),
    sa.Column('positive_count', sa.Integer(), nullable=False),
    sa.Column('negative_count', sa.Integer(), nullable=False),
    sa.Column('sufficient_evidence', sa.Boolean(), nullable=False),
    sa.Column('unanswered_areas', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('supporting_question_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('positive_evidence_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('negative_evidence_ids', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('transcript_refs', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('is_final', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_scores_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scores'))
    )
    op.create_index('ix_scores_session_category_key', 'scores', ['session_id', 'category', 'key'], unique=False)
    op.create_index(op.f('ix_scores_session_id'), 'scores', ['session_id'], unique=False)
    op.create_table('answers',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('question_id', sa.UUID(), nullable=True),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('transcript_text', sa.Text(), nullable=False),
    sa.Column('typed', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('started_ms', sa.BigInteger(), nullable=True),
    sa.Column('ended_ms', sa.BigInteger(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('word_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('stt_confidence', sa.Float(), nullable=True),
    sa.Column('audio_storage_key', sa.String(length=512), nullable=True),
    sa.Column('interrupted_ai', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('quick_assessment', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='received', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], name=op.f('fk_answers_question_id_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_answers_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_answers')),
    sa.UniqueConstraint('session_id', 'sequence', name='uq_answers_session_sequence')
    )
    op.create_index(op.f('ix_answers_question_id'), 'answers', ['question_id'], unique=False)
    op.create_index(op.f('ix_answers_session_id'), 'answers', ['session_id'], unique=False)
    op.create_table('coding_sessions',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('coding_question_id', sa.UUID(), nullable=False),
    sa.Column('question_id', sa.UUID(), nullable=True),
    sa.Column('language', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('time_limit_seconds', sa.Integer(), nullable=False),
    sa.Column('hints_available', sa.Integer(), server_default='3', nullable=False),
    sa.Column('hints_used', sa.Integer(), server_default='0', nullable=False),
    sa.Column('paste_event_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('final_code', sa.Text(), nullable=True),
    sa.Column('final_run_id', sa.UUID(), nullable=True),
    sa.Column('evaluation', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('observer_state', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['coding_question_id'], ['coding_questions.id'], name=op.f('fk_coding_sessions_coding_question_id_coding_questions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], name=op.f('fk_coding_sessions_question_id_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_coding_sessions_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_coding_sessions'))
    )
    op.create_index(op.f('ix_coding_sessions_session_id'), 'coding_sessions', ['session_id'], unique=False)
    op.create_table('integrity_events',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('organization_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.String(length=48), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('elapsed_seconds', sa.Float(), nullable=True),
    sa.Column('duration_ms', sa.BigInteger(), nullable=True),
    sa.Column('count', sa.BigInteger(), server_default='1', nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('evidence_media_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('review_status', sa.String(length=24), server_default='unreviewed', nullable=False),
    sa.Column('reviewed_by_id', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['evidence_media_id'], ['media_assets.id'], name=op.f('fk_integrity_events_evidence_media_id_media_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reviewed_by_id'], ['users.id'], name=op.f('fk_integrity_events_reviewed_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_integrity_events_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_integrity_events'))
    )
    op.create_index('ix_integrity_events_org_review', 'integrity_events', ['organization_id', 'review_status', 'occurred_at'], unique=False)
    op.create_index(op.f('ix_integrity_events_session_id'), 'integrity_events', ['session_id'], unique=False)
    op.create_index('ix_integrity_events_session_type', 'integrity_events', ['session_id', 'type'], unique=False)
    op.create_table('answer_evaluations',
    sa.Column('answer_id', sa.UUID(), nullable=False),
    sa.Column('question_id', sa.UUID(), nullable=True),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('model_call_id', sa.UUID(), nullable=True),
    sa.Column('overall_quality', sa.String(length=32), nullable=False),
    sa.Column('correctness', sa.Float(), nullable=True),
    sa.Column('relevance', sa.Float(), nullable=True),
    sa.Column('completeness', sa.Float(), nullable=True),
    sa.Column('depth', sa.Float(), nullable=True),
    sa.Column('specificity', sa.Float(), nullable=True),
    sa.Column('practical_experience', sa.Float(), nullable=True),
    sa.Column('reasoning', sa.Float(), nullable=True),
    sa.Column('consistency', sa.Float(), nullable=True),
    sa.Column('architecture_awareness', sa.Float(), nullable=True),
    sa.Column('tradeoff_understanding', sa.Float(), nullable=True),
    sa.Column('communication_clarity', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('is_fallback', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('evaluation', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['answers.id'], name=op.f('fk_answer_evaluations_answer_id_answers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], name=op.f('fk_answer_evaluations_question_id_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_answer_evaluations_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_answer_evaluations')),
    sa.UniqueConstraint('answer_id', name=op.f('uq_answer_evaluations_answer_id'))
    )
    op.create_index(op.f('ix_answer_evaluations_session_id'), 'answer_evaluations', ['session_id'], unique=False)
    op.create_table('code_snapshots',
    sa.Column('coding_session_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('language', sa.String(length=16), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('diff_from_previous', sa.Text(), nullable=True),
    sa.Column('lines_added', sa.Integer(), nullable=False),
    sa.Column('lines_removed', sa.Integer(), nullable=False),
    sa.Column('char_count', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(length=32), nullable=False),
    sa.Column('elapsed_seconds', sa.Float(), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['coding_session_id'], ['coding_sessions.id'], name=op.f('fk_code_snapshots_coding_session_id_coding_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_code_snapshots')),
    sa.UniqueConstraint('coding_session_id', 'seq', name='uq_code_snapshots_session_seq')
    )
    op.create_table('transcripts',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('speaker', sa.String(length=16), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('start_ms', sa.BigInteger(), nullable=True),
    sa.Column('end_ms', sa.BigInteger(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('question_id', sa.UUID(), nullable=True),
    sa.Column('answer_id', sa.UUID(), nullable=True),
    sa.Column('interrupted', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['answers.id'], name=op.f('fk_transcripts_answer_id_answers'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], name=op.f('fk_transcripts_question_id_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_transcripts_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transcripts')),
    sa.UniqueConstraint('session_id', 'seq', name='uq_transcripts_session_seq')
    )
    op.create_index('ix_transcripts_session_start', 'transcripts', ['session_id', 'start_ms'], unique=False)
    op.create_table('code_runs',
    sa.Column('coding_session_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('language', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('stdout', sa.Text(), nullable=True),
    sa.Column('stderr', sa.Text(), nullable=True),
    sa.Column('compile_output', sa.Text(), nullable=True),
    sa.Column('exit_code', sa.Integer(), nullable=True),
    sa.Column('test_results', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('passed_count', sa.Integer(), nullable=False),
    sa.Column('total_count', sa.Integer(), nullable=False),
    sa.Column('runtime_ms', sa.Integer(), nullable=True),
    sa.Column('memory_kb', sa.BigInteger(), nullable=True),
    sa.Column('elapsed_seconds', sa.Float(), nullable=True),
    sa.Column('sandbox_meta', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['coding_session_id'], ['coding_sessions.id'], name=op.f('fk_code_runs_coding_session_id_coding_sessions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['code_snapshots.id'], name=op.f('fk_code_runs_snapshot_id_code_snapshots'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_code_runs'))
    )
    op.create_index(op.f('ix_code_runs_coding_session_id'), 'code_runs', ['coding_session_id'], unique=False)
    op.create_index('ix_code_runs_session_created', 'code_runs', ['coding_session_id', 'created_at'], unique=False)
    op.create_table('coding_hints',
    sa.Column('coding_session_id', sa.UUID(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('requested_via', sa.String(length=16), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=True),
    sa.Column('elapsed_seconds', sa.Float(), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['coding_session_id'], ['coding_sessions.id'], name=op.f('fk_coding_hints_coding_session_id_coding_sessions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['code_snapshots.id'], name=op.f('fk_coding_hints_snapshot_id_code_snapshots'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_coding_hints')),
    sa.UniqueConstraint('coding_session_id', 'level', name='uq_coding_hints_session_level')
    )
    op.create_table('competency_evidence',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('competency_key', sa.String(length=80), nullable=False),
    sa.Column('question_id', sa.UUID(), nullable=True),
    sa.Column('answer_id', sa.UUID(), nullable=True),
    sa.Column('evaluation_id', sa.UUID(), nullable=True),
    sa.Column('coding_session_id', sa.UUID(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('signal', sa.String(length=16), nullable=False),
    sa.Column('dimension', sa.String(length=80), nullable=False),
    sa.Column('evidence', sa.Text(), nullable=False),
    sa.Column('strength', sa.Float(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('difficulty', sa.String(length=16), nullable=True),
    sa.Column('elapsed_seconds', sa.Float(), nullable=True),
    sa.Column('transcript_ref', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['answer_id'], ['answers.id'], name=op.f('fk_competency_evidence_answer_id_answers'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['coding_session_id'], ['coding_sessions.id'], name=op.f('fk_competency_evidence_coding_session_id_coding_sessions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['evaluation_id'], ['answer_evaluations.id'], name=op.f('fk_competency_evidence_evaluation_id_answer_evaluations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], name=op.f('fk_competency_evidence_question_id_questions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], name=op.f('fk_competency_evidence_session_id_interview_sessions'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_competency_evidence'))
    )
    op.create_index(op.f('ix_competency_evidence_session_id'), 'competency_evidence', ['session_id'], unique=False)
    op.create_index('ix_competency_evidence_session_key', 'competency_evidence', ['session_id', 'competency_key'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_competency_evidence_session_key', table_name='competency_evidence')
    op.drop_index(op.f('ix_competency_evidence_session_id'), table_name='competency_evidence')
    op.drop_table('competency_evidence')
    op.drop_table('coding_hints')
    op.drop_index('ix_code_runs_session_created', table_name='code_runs')
    op.drop_index(op.f('ix_code_runs_coding_session_id'), table_name='code_runs')
    op.drop_table('code_runs')
    op.drop_index('ix_transcripts_session_start', table_name='transcripts')
    op.drop_table('transcripts')
    op.drop_table('code_snapshots')
    op.drop_index(op.f('ix_answer_evaluations_session_id'), table_name='answer_evaluations')
    op.drop_table('answer_evaluations')
    op.drop_index('ix_integrity_events_session_type', table_name='integrity_events')
    op.drop_index(op.f('ix_integrity_events_session_id'), table_name='integrity_events')
    op.drop_index('ix_integrity_events_org_review', table_name='integrity_events')
    op.drop_table('integrity_events')
    op.drop_index(op.f('ix_coding_sessions_session_id'), table_name='coding_sessions')
    op.drop_table('coding_sessions')
    op.drop_index(op.f('ix_answers_session_id'), table_name='answers')
    op.drop_index(op.f('ix_answers_question_id'), table_name='answers')
    op.drop_table('answers')
    op.drop_index(op.f('ix_scores_session_id'), table_name='scores')
    op.drop_index('ix_scores_session_category_key', table_name='scores')
    op.drop_table('scores')
    op.drop_index('ix_score_snapshots_session_elapsed', table_name='score_snapshots')
    op.drop_table('score_snapshots')
    op.drop_index(op.f('ix_review_decisions_session_id'), table_name='review_decisions')
    op.drop_table('review_decisions')
    op.drop_index(op.f('ix_reports_session_id'), table_name='reports')
    op.drop_table('reports')
    op.drop_index(op.f('ix_questions_session_id'), table_name='questions')
    op.drop_table('questions')
    op.drop_index(op.f('ix_media_assets_session_id'), table_name='media_assets')
    op.drop_index(op.f('ix_media_assets_retention_until'), table_name='media_assets')
    op.drop_index(op.f('ix_media_assets_organization_id'), table_name='media_assets')
    op.drop_index(op.f('ix_media_assets_candidate_id'), table_name='media_assets')
    op.drop_table('media_assets')
    op.drop_index('ix_interview_events_session_type', table_name='interview_events')
    op.drop_table('interview_events')
    op.drop_index(op.f('ix_interview_sessions_organization_id'), table_name='interview_sessions')
    op.drop_index('ix_interview_sessions_org_status', table_name='interview_sessions')
    op.drop_index(op.f('ix_interview_sessions_job_id'), table_name='interview_sessions')
    op.drop_index(op.f('ix_interview_sessions_interview_id'), table_name='interview_sessions')
    op.drop_index(op.f('ix_interview_sessions_candidate_id'), table_name='interview_sessions')
    op.drop_table('interview_sessions')
    op.drop_index(op.f('ix_resume_chunks_resume_id'), table_name='resume_chunks')
    op.drop_index('ix_resume_chunks_embedding_hnsw', table_name='resume_chunks', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index(op.f('ix_resume_chunks_candidate_id'), table_name='resume_chunks')
    op.drop_table('resume_chunks')
    op.drop_index(op.f('ix_candidate_profiles_resume_id'), table_name='candidate_profiles')
    op.drop_index(op.f('ix_candidate_profiles_candidate_id'), table_name='candidate_profiles')
    op.drop_table('candidate_profiles')
    op.drop_index(op.f('ix_consents_interview_id'), table_name='consents')
    op.drop_index(op.f('ix_consents_candidate_id'), table_name='consents')
    op.drop_table('consents')
    op.drop_index(op.f('ix_candidate_resumes_sha256'), table_name='candidate_resumes')
    op.drop_index(op.f('ix_candidate_resumes_organization_id'), table_name='candidate_resumes')
    op.drop_index(op.f('ix_candidate_resumes_interview_id'), table_name='candidate_resumes')
    op.drop_index(op.f('ix_candidate_resumes_candidate_id'), table_name='candidate_resumes')
    op.drop_table('candidate_resumes')
    op.drop_index(op.f('ix_auth_sessions_user_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_interview_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_family_id'), table_name='auth_sessions')
    op.drop_index(op.f('ix_auth_sessions_candidate_id'), table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index(op.f('ix_interviews_organization_id'), table_name='interviews')
    op.drop_index('ix_interviews_org_status', table_name='interviews')
    op.drop_index(op.f('ix_interviews_job_id'), table_name='interviews')
    op.drop_index(op.f('ix_interviews_candidate_id'), table_name='interviews')
    op.drop_table('interviews')
    op.drop_index('ix_rounds_job_sequence', table_name='rounds')
    op.drop_index(op.f('ix_rounds_job_id'), table_name='rounds')
    op.drop_table('rounds')
    op.drop_index(op.f('ix_job_requirements_job_id'), table_name='job_requirements')
    op.drop_table('job_requirements')
    op.drop_index(op.f('ix_question_bank_organization_id'), table_name='question_bank')
    op.drop_index('ix_question_bank_embedding_hnsw', table_name='question_bank', postgresql_using='hnsw', postgresql_with={'m': 16, 'ef_construction': 64}, postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_index(op.f('ix_question_bank_competency_key'), table_name='question_bank')
    op.drop_index('ix_question_bank_competency_difficulty', table_name='question_bank')
    op.drop_table('question_bank')
    op.drop_index(op.f('ix_jobs_organization_id'), table_name='jobs')
    op.drop_index('ix_jobs_org_status', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_interview_templates_organization_id'), table_name='interview_templates')
    op.drop_table('interview_templates')
    op.drop_index(op.f('ix_candidates_organization_id'), table_name='candidates')
    op.drop_table('candidates')
    op.drop_index(op.f('ix_users_organization_id'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_competencies_organization_id'), table_name='competencies')
    op.drop_table('competencies')
    op.drop_index(op.f('ix_coding_questions_organization_id'), table_name='coding_questions')
    op.drop_table('coding_questions')
    op.drop_table('system_notes')
    op.drop_table('organizations')
    op.drop_index('ix_model_calls_task_created', table_name='model_calls')
    op.drop_index(op.f('ix_model_calls_session_id'), table_name='model_calls')
    op.drop_table('model_calls')
    op.drop_index('ix_background_jobs_status_type', table_name='background_jobs')
    op.drop_table('background_jobs')
    op.drop_index('ix_audit_logs_resource', table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_organization_id'), table_name='audit_logs')
    op.drop_index('ix_audit_logs_org_created', table_name='audit_logs')
    op.drop_table('audit_logs')
