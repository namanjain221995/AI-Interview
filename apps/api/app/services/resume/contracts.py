"""Resume pipeline contract used by the candidate portal and recruiter API.

Implemented in ``app.services.resume.service``. Upload returns quickly after
validation + storage; extraction, OCR, analysis and profile generation run in
the ``resume.process`` -> ``profile.generate`` background jobs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class ResumeProgress:
    resume_id: str
    status: str  # ResumeStatus value
    steps: list[dict[str, Any]] = field(default_factory=list)  # [{"key","label","state": pending|active|done|failed, "at"}]
    error: str | None = None  # candidate-safe message
    profile_ready: bool = False
    # Additive (2026-10-08): which profile sections show the deterministic quick
    # read vs the model's version ([{"key","label","state"}], see progress.py).
    sections: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class ResumeService(Protocol):
    async def accept_upload(
        self,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        candidate_id: uuid.UUID,
        interview_id: uuid.UUID | None,
        job_id: uuid.UUID | None,
        filename: str,
        declared_content_type: str | None,
        data: bytes,
        uploaded_by_candidate: bool,
    ) -> Any:  # CandidateResume
        """Validate (type sniffing, structure, active content, size, malware
        scan per MALWARE_SCAN_MODE), store, create the row, enqueue
        resume.process. Raises ValidationFailed with a candidate-safe message."""
        ...

    async def progress(self, db: AsyncSession, resume_id: uuid.UUID) -> ResumeProgress: ...

    async def latest_profile(self, db: AsyncSession, candidate_id: uuid.UUID, job_id: uuid.UUID | None = None) -> Any:
        """Latest ready CandidateProfile row (or None). Never a ``quick`` row
        (the deterministic profile shown while the model profile is generated)."""
        ...

    def candidate_safe_summary(self, profile: dict[str, Any]) -> dict[str, Any]:
        """What the candidate may see about their parsed resume: name, headline,
        experience years, roles (title/company/dates), top skills. Never the
        claims-to-verify list or job-match internals."""
        ...
