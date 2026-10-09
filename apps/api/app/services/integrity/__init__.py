"""Integrity monitoring and the consent disclosure.

Integrity signals are observations for human review, never automated
verdicts. Public entry points::

    from app.services.integrity import get_integrity_service, build_disclosure

    service = get_integrity_service()            # IntegrityService (contracts.py)
    outcomes = await service.record_client_events(session_id, events)
    outcome = await service.record_server_event(session_id, IntegrityEventType.DISCONNECT)
    media_id = await service.submit_frame(session_id, kind="camera", image=..., content_type=..., captured_at=...)

    disclosure = build_disclosure(interview.policy_snapshot, organization_name=..., job_title=...)
    body = disclosure_payload(disclosure, interview.policy_snapshot)   # + text_markdown, consent_items

Modules: ``rules`` (rule engine), ``vision`` (prompts + sustained-signal
state), ``frames`` (image validation), ``service`` (persistence, bus,
metrics), ``retention`` (``retention.sweep``), ``queries`` (counts for
scoring/reports), ``disclosure``.
"""

from app.services.integrity.contracts import (
    NON_TERMINATING_SOURCES,
    Disclosure,
    DisclosureBuilder,
    DisclosureItem,
    IntegrityOutcome,
    IntegrityService,
)
from app.services.integrity.disclosure import (
    DefaultDisclosureBuilder,
    build_disclosure,
    consent_items,
    disclosure_payload,
    missing_required_items,
    render_disclosure_markdown,
)
from app.services.integrity.queries import count_unreviewed_flags, integrity_summary
from app.services.integrity.service import (
    DefaultIntegrityService,
    get_integrity_service,
    set_integrity_service,
)

__all__ = [
    "NON_TERMINATING_SOURCES",
    "DefaultDisclosureBuilder",
    "DefaultIntegrityService",
    "Disclosure",
    "DisclosureBuilder",
    "DisclosureItem",
    "IntegrityOutcome",
    "IntegrityService",
    "build_disclosure",
    "consent_items",
    "count_unreviewed_flags",
    "disclosure_payload",
    "get_integrity_service",
    "integrity_summary",
    "missing_required_items",
    "render_disclosure_markdown",
    "set_integrity_service",
]
