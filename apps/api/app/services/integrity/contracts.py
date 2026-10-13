"""Seam between the realtime runtime / portal API and the integrity module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from app.domain.enums import IntegrityAction, IntegrityEventType, IntegritySeverity, IntegritySource
from app.schemas.realtime import ClientIntegrityEvent


@dataclass(slots=True)
class IntegrityOutcome:
    """Result of evaluating a signal against the frozen policy.

    ``candidate_message`` is set only when policy says to warn the candidate;
    ``action`` PAUSE/TERMINATE are only ever produced for objective,
    recruiter-configured conditions (e.g. required screen sharing stopped),
    never from vision-model guesses."""

    event_id: str | None
    event_type: IntegrityEventType
    severity: IntegritySeverity
    action: IntegrityAction
    candidate_message: str | None = None


@runtime_checkable
class IntegrityService(Protocol):
    async def record_client_events(self, session_id: str, events: list[ClientIntegrityEvent]) -> list[IntegrityOutcome]:
        """Normalize, rate-limit, aggregate and persist browser signals."""
        ...

    async def record_server_event(
        self, session_id: str, event_type: IntegrityEventType, *, details: dict[str, Any] | None = None
    ) -> IntegrityOutcome | None:
        """Server-observed signals (disconnect, reconnect, reload)."""
        ...

    async def submit_frame(
        self, session_id: str, *, kind: Literal["camera", "screen"], image: bytes, content_type: str, captured_at: str
    ) -> str:
        """Store (per retention policy) and enqueue analysis; returns media id."""
        ...

    async def analyze_frame(self, session_id: str, media_id: str) -> list[IntegrityOutcome]:
        """Worker: vision model observation -> sustained-signal rules -> events."""
        ...


# Event sources never allowed to terminate an interview automatically.
NON_TERMINATING_SOURCES: frozenset[IntegritySource] = frozenset({IntegritySource.VISION, IntegritySource.SCREEN})


@dataclass(slots=True)
class DisclosureItem:
    key: str  # microphone | camera | transcript | integrity_monitoring | camera_frames | screen_share | audio_recording | video_recording | ai_assessment | retention
    title: str
    description: str
    required: bool
    enabled: bool
    # One plain sentence shown by default on the consent page (the description stays one click away); derived
    # from the same policy values as the description and covered by the policy hash.
    summary: str = ""


@dataclass(slots=True)
class Disclosure:
    """Exactly what the candidate is told before any recording or analysis.

    ``policy_hash`` = sha256 over the rendered text + the policy snapshot, and
    is echoed back with the consent so the record proves what was shown."""

    policy_version: str
    policy_hash: str
    title: str
    intro: str
    items: list[DisclosureItem]
    retention_summary: str
    purpose: str
    human_review_statement: str
    limitations_statement: str  # e.g. browser fullscreen cannot lock the operating system


class DisclosureBuilder(Protocol):
    def build(self, policy_snapshot: dict[str, Any], *, organization_name: str, job_title: str) -> Disclosure: ...
