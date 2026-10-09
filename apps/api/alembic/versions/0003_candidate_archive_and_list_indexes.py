"""Candidate archive (soft delete) and indexes for the list/analytics queries.

* ``candidates.archived_at``: archived candidates are hidden from lists by
  default and can be restored (permanent deletion removes the row).
* Indexes, each measured on a perf copy of the dev data (~4.4k candidates,
  2k sessions, 97k score rows, 211k model calls; 100k candidates for the
  ordering index):

  - ``ix_candidate_profiles_skills_trgm`` (GIN, pg_trgm): candidate search
    ``?q=`` matches skills with ``(profile ->> 'skills') ILIKE '%term%'``;
    a non-matching term went from a full scan of every profile (83 ms) to
    2.6 ms.
  - ``ix_candidates_org_created`` / ``ix_interviews_org_created`` /
    ``ix_interview_sessions_org_started``: the list pages read the newest rows
    of one organization through the index instead of sorting all of them
    (100k candidates: 33.7 ms -> 0.4 ms for a page).
  - ``ix_scores_session_created``: the newest score set of a session is an
    index-only probe (analytics no longer aggregate the whole scores table).
  - ``ix_model_calls_org_created``: model-usage analytics over a period.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("candidates", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_candidates_org_created", "candidates", ["organization_id", "created_at"])
    op.create_index(
        "ix_candidate_profiles_skills_trgm",
        "candidate_profiles",
        [sa.text("(profile ->> 'skills') gin_trgm_ops")],
        postgresql_using="gin",
    )
    op.create_index("ix_interviews_org_created", "interviews", ["organization_id", "created_at"])
    op.create_index(
        "ix_interview_sessions_org_started",
        "interview_sessions",
        ["organization_id", sa.text("started_at DESC NULLS LAST"), sa.text("created_at DESC")],
    )
    op.create_index("ix_scores_session_created", "scores", ["session_id", "created_at"])
    op.create_index("ix_model_calls_org_created", "model_calls", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_calls_org_created", table_name="model_calls")
    op.drop_index("ix_scores_session_created", table_name="scores")
    op.drop_index("ix_interview_sessions_org_started", table_name="interview_sessions")
    op.drop_index("ix_interviews_org_created", table_name="interviews")
    op.drop_index("ix_candidate_profiles_skills_trgm", table_name="candidate_profiles")
    op.drop_index("ix_candidates_org_created", table_name="candidates")
    op.drop_column("candidates", "archived_at")
