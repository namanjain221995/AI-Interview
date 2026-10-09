"""Human review items of an intake evaluation (design contract section 8.8, shape 15.8).

Review items are observations with evidence for a human reviewer. They never decide an outcome, never change a
score and never end an interview: the kit's "automatic red flags" (dishonest claims / self-rating collapse, cannot
read a simple passage, fails the mandatory gate, communication too unclear) arrive here as items with evidence.

Ids are deterministic (``ri_`` + sha1 of kind, competency, discriminator and sorted evidence ids, first 10 hex), so a
reviewer's resolution of an item survives a re-evaluation that raises the same item again.

| kind | source | trigger | severity |
|---|---|---|---|
| ``claims_not_supported`` (kit) | rule + evaluator drafts | a claim verdict unsupported/inconsistent with validated evidence; or self-rating >= 6 with the language check incorrect / unanswered | important |
| ``reading_not_completed`` (kit) | reading metrics | nothing read or Drill A completeness < 0.50 | important |
| ``work_authorization_review`` (kit) | onboarding | MQ1 enabled for this interview (evidence = the onboarding record) | important when not authorised or sponsorship needed, else info |
| ``mandatory_mismatch`` (kit) | onboarding + evaluator drafts | cannot commit to the training, not comfortable with the expected level, or not open to AI/ML with a non-AI/ML target; or an MQ2-MQ4 answer stating it | important |
| ``communication_unclear`` (kit) | rule | communication scored 1 with evidence from >= 2 stages | important |
| ``stage_incomplete`` / ``mandatory_item_missing`` | validator V5 | | attention |
| ``activity_interrupted`` | activity rows | an activity ended interrupted | attention |
| ``asr_quality_low`` | captures | recognition degraded / low confidence on an activity, or Drill A only from live segments | attention |
| ``integrity_observation`` | integrity | unreviewed flagged integrity signals | attention |
| ``evaluator_fallback`` / ``evaluator_disagreement`` | pipeline / V6 | | attention |
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.services.intake.eval_schemas import ReviewItemDraft
from app.services.intake.evidence import AliasMap, EvaluationInputs
from app.services.intake.kit import IntakeKit, load_intake_kit
from app.services.intake.relay import DrillScore
from app.services.intake.validator import CompetencyResult, Finding, evidence_ref

KIT_FLAGS = frozenset(
    {
        "claims_not_supported",
        "reading_not_completed",
        "work_authorization_review",
        "mandatory_mismatch",
        "communication_unclear",
    }
)
PLATFORM_TITLES: dict[str, str] = {
    "stage_incomplete": "Stage not completed",
    "mandatory_item_missing": "Mandatory item not covered",
    "activity_interrupted": "Activity interrupted",
    "asr_quality_low": "Speech recognition quality — listen to the recording",
    "integrity_observation": "Integrity observations to review",
    "evaluator_fallback": "Automatic evaluation unavailable for a competency",
    "evaluator_disagreement": "Scores that need a second look",
}
SEVERITY_ORDER = {"important": 0, "attention": 1, "info": 2}
LOW_STT_CONFIDENCE = 0.5
AI_ML_TARGETS = frozenset({"ai_ml"})


def review_item_id(kind: str, competency: str | None, key: str, evidence: Iterable[Mapping[str, Any]]) -> str:
    ids = sorted(
        str(
            ref.get("answer_id")
            or ref.get("activity_id")
            or ref.get("question_id")
            or ref.get("source")
            or ""
        )
        for ref in evidence
    )
    raw = "|".join([kind, competency or "", key, *ids])
    return "ri_" + hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


def make_item(
    kind: str,
    *,
    severity: str,
    detail: str,
    source: str,
    competency_keys: Sequence[str] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    key: str = "",
    kit: IntakeKit | None = None,
) -> dict[str, Any]:
    """One ``ReviewItem`` (contract 15.8) as a JSON-ready dict."""
    kit_flag = kind if kind in KIT_FLAGS else None
    if kit_flag is not None:
        kit = kit or load_intake_kit()
        title = kit.review_flag(kit_flag).label
    else:
        title = PLATFORM_TITLES.get(kind, kind.replace("_", " ").capitalize())
    competency = competency_keys[0] if competency_keys else None
    refs = [dict(ref) for ref in evidence][:6]
    return {
        "id": review_item_id(kind, competency, key, refs),
        "kind": kind,
        "severity": severity,
        "title": title,
        "detail": detail.strip()[:600],
        "competency_keys": list(competency_keys),
        "evidence": refs,
        "source": source,
        "kit_flag": kit_flag,
        "status": "open",
    }


def onboarding_ref(note: str | None = None) -> dict[str, Any]:
    return {
        "source": "onboarding",
        "stage": "mandatory",
        "item_key": None,
        "question_id": None,
        "answer_id": None,
        "activity_id": None,
        "start_ms": None,
        "end_ms": None,
        "quote": note,
        "supports": "context",
    }


def activity_ref(inputs: EvaluationInputs, aliases: AliasMap, kind: str) -> dict[str, Any] | None:
    activity = inputs.activities.get(kind)
    if activity is None:
        return None
    answer = inputs.activity_answer(kind)
    target = aliases.get(answer.alias) if answer is not None and answer.alias else None
    if target is not None:
        return evidence_ref(target, quote=" ".join(target.text.split()[:30]) or None, supports="context")
    capture = activity.capture or {}
    return {
        "source": "activity",
        "stage": activity.stage_key,
        "item_key": None,
        "question_id": str(activity.question_id) if activity.question_id else None,
        "answer_id": str(activity.answer_id) if activity.answer_id else None,
        "activity_id": str(activity.id),
        "start_ms": capture.get("started_ms"),
        "end_ms": capture.get("ended_ms"),
        "quote": None,
        "supports": "context",
    }


def _draft_refs(draft: ReviewItemDraft, aliases: AliasMap, allowed: frozenset[str]) -> list[dict[str, Any]]:
    refs = []
    for alias in draft.answers:
        target = aliases.get(alias)
        if target is None or target.kind != "answer" or target.alias not in allowed:
            continue
        refs.append(evidence_ref(target, quote=None, supports="context"))
    return refs


def build_review_items(
    inputs: EvaluationInputs,
    aliases: AliasMap,
    *,
    competencies: Mapping[str, CompetencyResult],
    claims: Sequence[Mapping[str, Any]],
    findings: Sequence[Finding],
    drill_a: DrillScore,
    drill_b: DrillScore,
    verification: Mapping[str, Any] | None = None,
    drafts: Mapping[str, Sequence[ReviewItemDraft]] | None = None,
    allowed: Mapping[str, frozenset[str]] | None = None,
    eligibility: Mapping[str, Any] | None = None,
    eligibility_enabled: bool = False,
    onboarding: Mapping[str, Any] | None = None,
    integrity_unreviewed: int = 0,
    kit: IntakeKit | None = None,
) -> list[dict[str, Any]]:
    """Every review item of one evaluation, deduplicated by id, most important first.

    ``eligibility`` is read only to set the severity of the work-authorisation item (never copied into it);
    ``onboarding`` is the stored onboarding record (non-eligibility fields)."""
    kit = kit or load_intake_kit()
    allowed = allowed or {}
    drafts = drafts or {}
    items: list[dict[str, Any]] = []

    # --- claims_not_supported (rule): claim verdicts with validated evidence
    for claim in claims:
        if claim.get("verdict") in ("unsupported", "inconsistent") and claim.get("evidence"):
            note = f" {claim['note']}" if claim.get("note") else ""
            items.append(
                make_item(
                    "claims_not_supported",
                    severity="important",
                    detail=f"Résumé claim {claim['claim_ref']} (“{claim.get('claim')}”): the follow-up answers "
                    f"were judged {claim['verdict'].replace('_', ' ')}.{note}",
                    source="rule",
                    competency_keys=("technical",),
                    evidence=claim["evidence"],
                    key=f"claim:{claim['claim_ref']}",
                    kit=kit,
                )
            )
    # --- claims_not_supported (rule): self-rating vs the language check
    expected = kit.language_verification.expected_from_rating
    quality = (verification or {}).get("quality")
    if (
        inputs.self_rating is not None
        and inputs.self_rating >= expected
        and quality in ("incorrect", "no_answer")
    ):
        evidence = (verification or {}).get("evidence")
        refs = [evidence] if evidence else []
        items.append(
            make_item(
                "claims_not_supported",
                severity="important",
                detail=f"Self-rating {inputs.self_rating}/10 on the form (a rating of {expected}+ should make the "
                f"language check easy); the language check answer was judged {quality.replace('_', ' ')}.",
                source="rule",
                competency_keys=("technical",),
                evidence=refs,
                key="self_rating",
                kit=kit,
            )
        )
    # --- evaluator drafts (claims_not_supported / mandatory_mismatch / communication_unclear)
    covered_answers: dict[str, set[str]] = {}
    for item in items:
        covered_answers.setdefault(item["kind"], set()).update(
            str(r.get("answer_id")) for r in item["evidence"] if r.get("answer_id")
        )
    evaluator_competency = {
        "resume_evidence": ("technical",),
        "technical": ("technical",),
        "communication": ("communication",),
        "trainability": ("trainability", "adaptability"),
    }
    for evaluator, evaluator_drafts in drafts.items():
        for draft in evaluator_drafts:
            if draft.kind == "evaluator_note":
                continue  # kept in the evaluation summary, not a review item kind
            refs = _draft_refs(draft, aliases, allowed.get(evaluator, frozenset()))
            if not refs:
                continue  # an item must carry validated evidence
            answer_ids = {str(r["answer_id"]) for r in refs if r.get("answer_id")}
            if answer_ids and answer_ids <= covered_answers.get(draft.kind, set()):
                continue  # the rule already raised it with the same answers
            items.append(
                make_item(
                    draft.kind,
                    severity="important",
                    detail=draft.note,
                    source="evaluator",
                    competency_keys=evaluator_competency.get(evaluator, ()),
                    evidence=refs,
                    key=f"draft:{evaluator}",
                    kit=kit,
                )
            )
            covered_answers.setdefault(draft.kind, set()).update(answer_ids)

    # --- reading_not_completed
    if "reading_not_completed" in drill_a.flags:
        ref = activity_ref(inputs, aliases, "reading_aloud")
        completeness = f" (completeness {drill_a.coverage:.2f})" if drill_a.coverage is not None else ""
        items.append(
            make_item(
                "reading_not_completed",
                severity="important",
                detail=f"Drill A: nothing was read or less than half of the passage was read{completeness}. "
                "Pronunciation and accent are never part of this check.",
                source="reading",
                competency_keys=("technical_reading",),
                evidence=[ref] if ref else [],
                key="drill_a",
                kit=kit,
            )
        )

    # --- work_authorization_review (MQ1 enabled): severity from the answers, no answer copied
    if eligibility_enabled:
        concern = bool(
            eligibility
            and (eligibility.get("authorized") is False or eligibility.get("needs_sponsorship") is True)
        )
        unanswered = not eligibility or eligibility.get("authorized") is None
        items.append(
            make_item(
                "work_authorization_review",
                severity="important" if concern else "info",
                detail="MQ1 is enabled for this interview. Review the onboarding answers against the placement "
                "policy"
                + (" (the candidate preferred to discuss it with the hiring team)" if unanswered else "")
                + ". They were never sent to a model or used in any score.",
                source="rule",
                evidence=[onboarding_ref()],
                key="mq1",
                kit=kit,
            )
        )

    # --- mandatory_mismatch from the onboarding form (MQ2-MQ4 areas)
    record = onboarding or {}
    mismatches = []
    if record.get("training_commitment") == "cannot":
        mismatches.append("cannot commit to the training")
    if record.get("experience_level_comfort") == "no":
        mismatches.append("not comfortable with the responsibilities expected at the target level")
    if record.get("open_to_ai_ml") == "no" and record.get("target_role") not in AI_ML_TARGETS:
        mismatches.append("not open to moving into AI/ML, with a non-AI/ML target role")
    if mismatches:
        items.append(
            make_item(
                "mandatory_mismatch",
                severity="important",
                detail="Onboarding form: " + "; ".join(mismatches) + ".",
                source="rule",
                competency_keys=("adaptability", "trainability"),
                evidence=[onboarding_ref("; ".join(mismatches))],
                key="onboarding",
                kit=kit,
            )
        )

    # --- communication_unclear: communication = 1 with evidence from >= 2 stages
    communication = competencies.get("communication")
    if communication is not None and communication.score is not None and communication.score == 1:
        stages = {str(ref["stage"]) for ref in communication.evidence if ref.get("stage")}
        if len(stages) >= 2:
            items.append(
                make_item(
                    "communication_unclear",
                    severity="important",
                    detail="Communication was scored 1 with excerpts from "
                    f"{len(stages)} stages ({', '.join(sorted(stages))}). Accent, pronunciation and appearance "
                    "are never part of this judgement.",
                    source="rule",
                    competency_keys=("communication",),
                    evidence=communication.evidence,
                    key="communication",
                    kit=kit,
                )
            )

    # --- validator findings (V5 / V6 / fallbacks)
    for finding in findings:
        items.append(
            make_item(
                finding.kind,
                severity="attention",
                detail=finding.detail,
                source="validator" if finding.kind != "evaluator_fallback" else "rule",
                competency_keys=finding.competency_keys,
                evidence=finding.evidence,
                key=finding.key,
                kit=kit,
            )
        )

    # --- activity_interrupted / asr_quality_low
    for kind, activity in sorted(inputs.activities.items()):
        ref = activity_ref(inputs, aliases, kind)
        if activity.status == "interrupted":
            items.append(
                make_item(
                    "activity_interrupted",
                    severity="attention",
                    detail=f"The {kind.replace('_', ' ')} activity ended interrupted after "
                    f"{len(activity.attempts_log) + 1} attempt(s) (disconnects).",
                    source="rule",
                    competency_keys=("technical_reading",)
                    if kind != "topic_card"
                    else ("structured_answer",),
                    evidence=[ref] if ref else [],
                    key=f"activity:{kind}",
                    kit=kit,
                )
            )
        capture = activity.capture or {}
        confidence = capture.get("stt_confidence")
        reasons = []
        if capture.get("stt_degraded"):
            reasons.append("speech recognition was degraded during the capture")
        if isinstance(confidence, int | float) and confidence < LOW_STT_CONFIDENCE:
            reasons.append(f"recognition confidence {confidence:.2f}")
        if kind == "reading_aloud" and activity.captured and activity.transcript_origin == "live_segments":
            reasons.append(
                "Drill A was aligned to the live recognition segments (the final pass did not run)"
            )
        if reasons:
            items.append(
                make_item(
                    "asr_quality_low",
                    severity="attention",
                    detail=f"{kind.replace('_', ' ').capitalize()}: "
                    + "; ".join(reasons)
                    + ". Listen to the recording "
                    "before relying on the transcript-based measures.",
                    source="rule",
                    competency_keys=("technical_reading",)
                    if kind != "topic_card"
                    else ("structured_answer",),
                    evidence=[ref] if ref else [],
                    key=f"asr:{kind}",
                    kit=kit,
                )
            )

    # --- integrity observations
    if integrity_unreviewed > 0:
        items.append(
            make_item(
                "integrity_observation",
                severity="attention",
                detail=f"{integrity_unreviewed} flagged integrity signal(s) have not been reviewed yet. Signals are "
                "observations, not determinations.",
                source="rule",
                key="integrity",
                kit=kit,
            )
        )

    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        unique.setdefault(item["id"], item)
    return sorted(unique.values(), key=lambda i: (SEVERITY_ORDER.get(i["severity"], 9), i["kind"], i["id"]))


def report_safe_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Review items for a report: the work-authorisation item keeps no severity hint of the MQ1 answers."""
    out = []
    for item in items:
        copy = dict(item)
        if copy.get("kind") == "work_authorization_review":
            copy["severity"] = "info"
            copy["detail"] = (
                "MQ1 is enabled for this interview. The answers are reviewed by people in the intake record only; "
                "they are not part of this report or any score."
            )
            copy["evidence"] = []
        out.append(copy)
    return out


__all__ = [
    "KIT_FLAGS",
    "PLATFORM_TITLES",
    "build_review_items",
    "make_item",
    "onboarding_ref",
    "report_safe_items",
    "review_item_id",
]
