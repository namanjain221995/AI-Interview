"""Permanent deletion of an open interview link (ADMIN).

Safe by design: a link that already produced a candidate is refused with 409
``link_has_registrations`` (close the link instead, or delete those candidates
first). Deleting a link never touches a candidate, interview or session.
Registrations without a candidate (blocked or abandoned attempts, which hold a
name, e-mail or phone) are deleted with the link (database cascade).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict
from app.core.logging import get_logger
from app.db.models import InterviewLink, LinkRegistration

log = get_logger(__name__)


@dataclass(slots=True)
class LinkDeletionSummary:
    """Counts only (it is audited)."""

    link_id: str
    previous_status: str
    registrations_deleted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def delete_link(db: AsyncSession, link: InterviewLink) -> LinkDeletionSummary:
    """Delete ``link`` (row-locked) unless a registration of it became a candidate."""
    with_candidate = int(
        await db.scalar(
            select(func.count())
            .select_from(LinkRegistration)
            .where(LinkRegistration.link_id == link.id, LinkRegistration.candidate_id.is_not(None))
        )
        or 0
    )
    if with_candidate:
        raise Conflict(
            "People have already registered through this link. Close the link instead, "
            "or delete those candidates first.",
            code="link_has_registrations",
            details={"registrations": with_candidate},
        )
    summary = LinkDeletionSummary(link_id=str(link.id), previous_status=link.status)
    summary.registrations_deleted = int(
        await db.scalar(
            select(func.count()).select_from(LinkRegistration).where(LinkRegistration.link_id == link.id)
        )
        or 0
    )
    link_id = link.id
    await db.execute(
        delete(InterviewLink).where(InterviewLink.id == link_id).execution_options(synchronize_session=False)
    )
    db.expunge(link)
    await db.flush()
    log.info("link.deleted", link_id=str(link_id), registrations=summary.registrations_deleted)
    return summary
