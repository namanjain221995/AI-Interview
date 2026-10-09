"""Initial ``IntakeState`` of a new intake session (design contract 6.8, ``SessionService._initial_state``).

``build_initial_intake_state`` reads the intake row of the interview (``intake_interviews``, T3), the frozen
invitation settings, the onboarding digest (never MQ1, never the full name), the organisation's approved program
information, and plans the seven stages with their budgets. Résumé claims (stage 2) are selected later, in
``session.prepare`` (``IntakeConductor.prepare``), from the effective profile.

T3 helpers (``ensure_intake_for_interview``, ``onboarding_digest``, ``effective_profile``) are imported lazily:
the intake row is created by T3's code only (single writer per table); the digest and effective-profile helpers
have small local equivalents used only when T3's module is unavailable (same rules: ``ONBOARDING_DIGEST_KEYS``,
truncation, labels).
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Conflict
from app.core.logging import get_logger
from app.db.models import IntakeInterview, IntakeOrgSettings
from app.schemas.intake import (
    DIGEST_SCRIPTING_DETAILS_MAX,
    DIGEST_TECHNICAL_BACKGROUND_MAX,
    INTAKE_DEFAULT_MINUTES,
    INTAKE_DEFAULT_PROGRAM_NAME,
    INTAKE_MAX_EXTRA_MINUTES,
    INTAKE_MAX_MINUTES,
    INTAKE_MIN_MINUTES,
    ONBOARDING_DIGEST_KEYS,
    PRIMARY_LANGUAGE_LABELS,
    IntakeInviteSettings,
    IntakeRoundConfig,
)
from app.schemas.rounds import RoundConfig
from app.services.intake.kit import load_intake_kit
from app.services.intake.prompts import assert_prompt_safe_digest
from app.services.intake.selection import resolve_language
from app.services.intake.stages import build_stages
from app.services.intake.state import IntakeState

log = get_logger(__name__)


def is_intake_round(round_config: RoundConfig | None) -> bool:
    return (
        round_config is not None
        and str(getattr(round_config.round_type, "value", round_config.round_type)) == "intake"
    )


def _t3(module: str, name: str) -> Any | None:
    try:
        return getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError):
        return None


def invite_settings(raw: Mapping[str, Any] | None) -> IntakeInviteSettings:
    """The frozen invitation settings (lenient: invalid or missing values take the defaults)."""
    data = dict(raw or {})
    try:
        return IntakeInviteSettings.model_validate(data)
    except ValidationError:
        allowed = set(IntakeInviteSettings.model_fields)
        cleaned = {k: v for k, v in data.items() if k in allowed}
        for key in list(cleaned):
            try:
                IntakeInviteSettings.model_validate({key: cleaned[key]})
            except ValidationError:
                cleaned.pop(key)
        log.warning("intake.invite_settings_invalid")
        return IntakeInviteSettings.model_validate(cleaned)


def local_digest(onboarding: Mapping[str, Any] | None) -> dict[str, Any]:
    """Prompt-safe onboarding subset (same rules as T3's ``onboarding.onboarding_digest``)."""
    if not onboarding:
        return {}
    out: dict[str, Any] = {}
    for key in ONBOARDING_DIGEST_KEYS:
        value = onboarding.get(key)
        if value in (None, ""):
            continue
        out[key] = value
    if "technical_background" in out:
        out["technical_background"] = str(out["technical_background"])[:DIGEST_TECHNICAL_BACKGROUND_MAX]
    if "scripting_details" in out:
        out["scripting_details"] = str(out["scripting_details"])[:DIGEST_SCRIPTING_DETAILS_MAX]
    code = str(onboarding.get("primary_language") or "")
    if code == "other" and onboarding.get("primary_language_other"):
        out["primary_language"] = str(onboarding["primary_language_other"])[:40]
    elif code in PRIMARY_LANGUAGE_LABELS:
        out["primary_language"] = PRIMARY_LANGUAGE_LABELS[code]
    return out


def onboarding_digest(row: IntakeInterview | None) -> dict[str, Any]:
    if row is None or not row.onboarding:
        return {}
    helper = _t3("app.services.intake.onboarding", "onboarding_digest")
    digest: Any = None
    if helper is not None:
        try:
            digest = helper(row.onboarding)
        except Exception as exc:  # noqa: BLE001 - fall back to the local subset (same keys)
            log.warning("intake.digest_helper_failed", error=type(exc).__name__)
    if digest is None:
        digest = local_digest(row.onboarding)
    if hasattr(digest, "model_dump"):
        digest = digest.model_dump(mode="json", exclude_none=True)
    digest = {k: v for k, v in dict(digest).items() if k in ONBOARDING_DIGEST_KEYS and v not in (None, "")}
    assert_prompt_safe_digest(digest)
    return digest


def effective_profile(profile_doc: Mapping[str, Any] | None, row: IntakeInterview | None) -> dict[str, Any]:
    """The candidate profile with the résumé-review corrections applied (T3), else the profile as stored."""
    profile = dict(profile_doc or {})
    record = row.resume_review if row is not None else None
    helper = _t3("app.services.intake.resume_review", "effective_profile")
    if helper is None or not record:
        return profile
    try:
        result = helper(profile, record)
    except Exception as exc:  # noqa: BLE001 - the stored profile is still usable
        log.warning("intake.effective_profile_failed", error=type(exc).__name__)
        return profile
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    return dict(result or profile)


async def intake_row(db: AsyncSession, interview_id: Any) -> IntakeInterview | None:
    return await db.scalar(
        select(IntakeInterview).where(IntakeInterview.interview_id == uuid.UUID(str(interview_id)))
    )


def _first_name(full_name: str | None) -> str | None:
    if not full_name or not str(full_name).strip():
        return None
    return str(full_name).strip().split()[0][:60]


def apply_row(state: IntakeState, row: IntakeInterview, *, org: IntakeOrgSettings | None) -> None:
    """Refresh the form-derived parts of ``state`` from the intake row (session init and prepare)."""
    kit = load_intake_kit()
    settings = invite_settings(row.settings)
    digest = onboarding_digest(row)
    state.onboarding_digest = digest
    state.work_authorization_enabled = bool(settings.work_authorization.enabled)
    state.presence = settings.presence
    state.program_name = settings.program_name or (org.program_name if org else INTAKE_DEFAULT_PROGRAM_NAME)
    state.approved_info = [
        {"topic": str(i.get("topic") or "")[:120], "answer": str(i.get("answer") or "")[:1200]}
        for i in ((org.approved_info if org is not None else None) or [])[:30]
        if isinstance(i, Mapping)
    ]
    state.target_role = row.target_role or digest.get("target_role") or state.target_role
    onboarding = dict(row.onboarding or {})
    state.first_name = _first_name(onboarding.get("full_name")) or state.first_name
    rating = digest.get("self_rating")
    if isinstance(rating, int | float) and 1 <= int(rating) <= 10:
        state.self_rating = int(rating)
    years = digest.get("years_hands_on")
    if isinstance(years, int | float):
        state.years_hands_on = float(years)
    language = resolve_language(kit, digest.get("primary_language"), onboarding.get("primary_language_other"))
    if language is not None:
        state.language = language.key
    if settings.kit_sha256:
        state.kit_sha256 = settings.kit_sha256


def duration_minutes(settings: IntakeInviteSettings, round_config: RoundConfig) -> int:
    minutes = settings.duration_minutes or round_config.duration_minutes or INTAKE_DEFAULT_MINUTES
    return max(INTAKE_MIN_MINUTES, min(INTAKE_MAX_MINUTES, int(minutes)))


async def build_initial_intake_state(
    db: AsyncSession,
    *,
    interview: Any,
    round_config: RoundConfig,
    candidate_name: str | None = None,
) -> tuple[IntakeState, float, float]:
    """``(IntakeState, time_limit_s, max_time_s)`` for a new intake session.

    409 ``intake_disabled`` when ``INTAKE_ENABLED`` is off; 409 ``intake_not_configured`` when the interview has
    no intake row and T3's ``ensure_intake_for_interview`` cannot create one."""
    if not get_settings().intake_enabled:
        raise Conflict("Intake interviews are turned off for this workspace.", code="intake_disabled")
    row = await intake_row(db, interview.id)
    if row is None:
        ensure = _t3("app.services.intake.invitations", "ensure_intake_for_interview")
        if ensure is not None:
            row = await ensure(db, interview)
    if row is None:
        raise Conflict(
            "This intake interview is not set up yet. Please contact the recruiter.",
            code="intake_not_configured",
        )
    org = await db.get(IntakeOrgSettings, interview.organization_id)
    kit = load_intake_kit()
    settings = invite_settings(row.settings)
    intake_cfg = round_config.intake or IntakeRoundConfig()
    minutes = duration_minutes(settings, round_config)
    time_limit_s = float(minutes * 60)
    max_time_s = time_limit_s + INTAKE_MAX_EXTRA_MINUTES * 60
    state = IntakeState(
        intake_id=str(row.id),
        kit_version=settings.kit_version or kit.version,
        kit_sha256=settings.kit_sha256 or kit.source.sha256,
        time_limit_s=time_limit_s,
        limits=intake_cfg.model_dump(mode="json"),
        overrides={
            "passage_drill_a": intake_cfg.passage_drill_a,
            "passage_drill_b": intake_cfg.passage_drill_b,
            "topic_card": intake_cfg.topic_card,
        },
        first_name=_first_name(candidate_name),
    )
    apply_row(state, row, org=org)
    state.stages = build_stages(
        time_limit_s,
        claims=[],
        target_role=state.target_role,
        work_authorization_enabled=state.work_authorization_enabled and row.eligibility is not None,
        clarify_per_rung=state.limit("resume_clarify_per_rung", 1),
    )
    log.info(
        "intake.session_state_initialised",
        intake_id=str(row.id),
        minutes=minutes,
        work_authorization=state.work_authorization_enabled,
        has_onboarding=bool(state.onboarding_digest),
    )
    return state, time_limit_s, max_time_s


__all__ = [
    "apply_row",
    "build_initial_intake_state",
    "duration_minutes",
    "effective_profile",
    "intake_row",
    "invite_settings",
    "is_intake_round",
    "local_digest",
    "onboarding_digest",
]
