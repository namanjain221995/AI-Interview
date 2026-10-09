"""Job match: deterministic requirement evidence blended with the model's view.

Deterministic evidence, per job requirement ``r``
    * semantic ``sem_r`` - the requirement text is embedded as a query
      (instruction-formatted) and searched with pgvector over this resume's
      ``resume_chunks``; the best chunks are reranked and the top reranker
      probability is used. If the reranker is down, the cosine similarity is
      calibrated instead: ``clamp((cos - 0.35) / 0.40, 0, 1)``.
    * lexical ``lex_r`` - 1.0 when the requirement's canonical name is a listed
      skill or appears verbatim in the resume, otherwise 0.7 x the share of its
      significant words present.
    * ``s_r = max(sem_r, 0.85 x lex_r)``: a literal mention proves exposure
      but not depth, so it can never reach the "strong" band alone.

Coverage ``D`` (0-100)
    ``D = 100 x sum(k_r x w_r x s_r) / sum(k_r x w_r)`` with the requirement
    weight ``w_r`` and kind factor ``k_r`` (required skill / qualification 1.0,
    responsibility 0.75, preferred skill 0.5).

Experience fit ``E`` (0-100, only when the job states a minimum)
    ``E = 100 x min(1, computed_years / experience_min_years)``. Exceeding the
    maximum is never penalised.

Model fit ``M`` (0-100)
    ``JobMatch.overall_fit`` from the profile-analysis model.

Blend
    ``score = (0.50 D + 0.35 M + 0.15 E) / (sum of the weights present)`` -
    a missing component's weight is redistributed proportionally (no
    requirements -> M and E only; model unavailable -> D and E only).

Per requirement, the reported strength blends both views:
``b_r = 0.5 s_r + 0.5 m_r`` (``m_r``: strong 1.0, partial 0.6, weak 0.3,
none 0) when the model assessed ``r``, else ``s_r``; bands: strong >= 0.70,
partial >= 0.45, weak >= 0.20, else none. Required skills and
qualifications below 0.20 are listed as missing.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIError, AIServices, CallContext
from app.core.logging import get_logger
from app.db.models import ResumeChunk
from app.domain.enums import ModelTask, RequirementKind
from app.schemas.ai import JobMatch, RequirementMatch
from app.services.resume.skills import canonical_key
from app.services.retrieval import embed_query, vector_search

log = get_logger(__name__)

EVIDENCE_INSTRUCTION = (
    "Given a job requirement, retrieve resume passages that show the candidate's experience with it"
)
KIND_FACTOR: dict[str, float] = {
    RequirementKind.REQUIRED_SKILL.value: 1.0,
    RequirementKind.QUALIFICATION.value: 1.0,
    RequirementKind.RESPONSIBILITY.value: 0.75,
    RequirementKind.PREFERRED_SKILL.value: 0.5,
}
MUST_HAVE_KINDS = frozenset({RequirementKind.REQUIRED_SKILL.value, RequirementKind.QUALIFICATION.value})
STRENGTH_VALUE: dict[str, float] = {"strong": 1.0, "partial": 0.6, "weak": 0.3, "none": 0.0}
BANDS: tuple[tuple[float, str], ...] = ((0.70, "strong"), (0.45, "partial"), (0.20, "weak"))
WEIGHT_COVERAGE = 0.50
WEIGHT_MODEL = 0.35
WEIGHT_EXPERIENCE = 0.15
LEXICAL_ONLY_CAP = 0.85
CHUNKS_PER_REQUIREMENT = 4
COSINE_FLOOR = 0.35
COSINE_SPAN = 0.40

_STOPWORDS = frozenset(
    "a an and or of the to in on for with experience years year knowledge strong solid proven ability using use "
    "skills skill familiarity understanding working hands production plus".split()
)
_WORD = re.compile(r"[a-z0-9+#.]+")


@dataclass(slots=True)
class RequirementInput:
    id: str
    name: str
    kind: str
    description: str | None = None
    weight: float = 1.0
    min_years: float | None = None
    competency_key: str | None = None

    @property
    def query(self) -> str:
        body = self.name if not self.description else f"{self.name}. {self.description[:300]}"
        if self.min_years:
            body += f" ({self.min_years:g}+ years)"
        return body


@dataclass(slots=True)
class RequirementEvidence:
    requirement: RequirementInput
    semantic: float = 0.0
    lexical: float = 0.0
    strength: float = 0.0
    best_chunk: str | None = None
    best_section: str | None = None
    reranked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement.id,
            "name": self.requirement.name,
            "semantic": round(self.semantic, 3),
            "lexical": round(self.lexical, 3),
            "strength": round(self.strength, 3),
            "section": self.best_section,
            "reranked": self.reranked,
        }


@dataclass(slots=True)
class MatchBreakdown:
    score: float | None
    coverage: float | None
    model_fit: float | None
    experience_fit: float | None
    weights: dict[str, float] = field(default_factory=dict)
    bands: dict[str, int] = field(default_factory=dict)
    requirements: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "coverage": self.coverage,
            "model_fit": self.model_fit,
            "experience_fit": self.experience_fit,
            "weights": self.weights,
            "bands": self.bands,
            "requirements": self.requirements,
        }


def band(value: float) -> str:
    for threshold, name in BANDS:
        if value >= threshold:
            return name
    return "none"


def _words(text: str) -> list[str]:
    return [
        w.strip(".") for w in _WORD.findall(text.lower()) if w.strip(".") and w.strip(".") not in _STOPWORDS
    ]


def lexical_score(name: str, resume_text_lower: str, skill_keys: set[str]) -> float:
    key = canonical_key(name)
    if key and key in skill_keys:
        return 1.0
    phrase = name.strip().lower()
    if phrase and re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", resume_text_lower):
        return 1.0
    words = [w for w in _words(name) if len(w) >= 2]
    if not words:
        return 0.0
    present = sum(
        1 for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", resume_text_lower)
    )
    return 0.7 * present / len(words)


def calibrate_cosine(similarity: float | None) -> float:
    if similarity is None:
        return 0.0
    return max(0.0, min(1.0, (similarity - COSINE_FLOOR) / COSINE_SPAN))


@dataclass(slots=True)
class SemanticHit:
    """Best retrieved chunk for one requirement (semantic part of the evidence)."""

    semantic: float = 0.0
    best_chunk: str | None = None
    best_section: str | None = None
    reranked: bool = False


async def semantic_evidence(
    db: AsyncSession,
    *,
    resume_id: Any,
    requirements: Sequence[RequirementInput],
    ai: AIServices,
    organization_id: str | None = None,
) -> list[SemanticHit]:
    """Semantic evidence per requirement: pgvector over the resume's chunks,
    reranked (needs the chunks, not the model profile, so it runs while the
    profile's model calls are still running)."""
    if not requirements:
        return []
    context = CallContext(task=ModelTask.EMBEDDING, organization_id=organization_id)
    try:
        vectors = await asyncio.gather(
            *(
                embed_query(ai.embeddings, req.query, EVIDENCE_INSTRUCTION, context=context)
                for req in requirements
            )
        )
    except AIError as exc:
        log.warning("resume.match.embedding_failed", error=type(exc).__name__)
        vectors = [None] * len(requirements)  # type: ignore[list-item]
    hits_per_requirement: list[list[Any]] = []
    for vector in vectors:
        if vector is None:
            hits_per_requirement.append([])
            continue
        hits = await vector_search(
            db, ResumeChunk, vector, filters={"resume_id": resume_id}, limit=CHUNKS_PER_REQUIREMENT
        )
        hits_per_requirement.append(hits)

    rerank_context = CallContext(task=ModelTask.RERANK, organization_id=organization_id)

    async def rerank(req: RequirementInput, hits: list[Any]) -> list[float | None]:
        if not hits:
            return []
        try:
            scores = await ai.reranker.rerank(
                req.query,
                [hit.item.text for hit in hits],
                instruction=EVIDENCE_INSTRUCTION,
                context=rerank_context,
            )
        except AIError as exc:
            log.info("resume.match.rerank_failed", error=type(exc).__name__)
            return [None] * len(hits)
        by_index: list[float | None] = [None] * len(hits)
        for score in scores:
            if 0 <= score.index < len(hits):
                by_index[score.index] = float(score.score)
        return by_index

    rerank_scores = await asyncio.gather(
        *(rerank(req, hits) for req, hits in zip(requirements, hits_per_requirement, strict=True))
    )
    out: list[SemanticHit] = []
    for hits, scores in zip(hits_per_requirement, rerank_scores, strict=True):
        hit_out = SemanticHit()
        best_hit = None
        best_value = 0.0
        for hit, score in zip(hits, scores, strict=False):
            value = score if score is not None else calibrate_cosine(hit.similarity)
            if score is not None:
                hit_out.reranked = True
            if value > best_value or best_hit is None:
                best_value, best_hit = value, hit
        hit_out.semantic = best_value
        if best_hit is not None:
            hit_out.best_chunk = best_hit.item.text
            hit_out.best_section = best_hit.item.section
        out.append(hit_out)
    return out


def combine_evidence(
    requirements: Sequence[RequirementInput],
    semantic: Sequence[SemanticHit] | None,
    *,
    resume_text: str,
    skill_names: Sequence[str],
) -> list[RequirementEvidence]:
    """Semantic hits + lexical presence -> per-requirement evidence (see module docstring)."""
    text_lower = resume_text.lower()
    skill_keys = {canonical_key(name) for name in skill_names if name}
    results: list[RequirementEvidence] = []
    for index, req in enumerate(requirements):
        hit = semantic[index] if semantic is not None and index < len(semantic) else SemanticHit()
        evidence = RequirementEvidence(
            requirement=req,
            semantic=hit.semantic,
            best_chunk=hit.best_chunk,
            best_section=hit.best_section,
            reranked=hit.reranked,
        )
        evidence.lexical = lexical_score(req.name, text_lower, skill_keys)
        evidence.strength = max(evidence.semantic, LEXICAL_ONLY_CAP * evidence.lexical)
        results.append(evidence)
    return results


async def requirement_evidence(
    db: AsyncSession,
    *,
    resume_id: Any,
    requirements: Sequence[RequirementInput],
    resume_text: str,
    skill_names: Sequence[str],
    ai: AIServices,
    organization_id: str | None = None,
) -> list[RequirementEvidence]:
    """Deterministic evidence for every requirement (see module docstring)."""
    if not requirements:
        return []
    semantic = await semantic_evidence(
        db, resume_id=resume_id, requirements=requirements, ai=ai, organization_id=organization_id
    )
    return combine_evidence(requirements, semantic, resume_text=resume_text, skill_names=skill_names)


def _match_model_entry(req: RequirementInput, entries: Sequence[RequirementMatch]) -> RequirementMatch | None:
    key = canonical_key(req.name)
    words = set(_words(req.name))
    best: tuple[float, RequirementMatch | None] = (0.0, None)
    for entry in entries:
        other = canonical_key(entry.requirement)
        if other == key:
            return entry
        if key and (key in other or other in key) and min(len(key), len(other)) >= 3:
            score = 0.9
        else:
            other_words = set(_words(entry.requirement))
            union = words | other_words
            score = len(words & other_words) / len(union) if union else 0.0
        if score > best[0]:
            best = (score, entry)
    return best[1] if best[0] >= 0.5 else None


def _snippet(text: str | None, limit: int = 300) -> str | None:
    if not text:
        return None
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rsplit(" ", 1)[0] + "\u2026"


def blend_job_match(
    evidence: Sequence[RequirementEvidence],
    model_match: JobMatch | None,
    *,
    experience_years: float | None,
    experience_min_years: float | None,
) -> tuple[JobMatch, MatchBreakdown]:
    """Combine deterministic evidence with the model's assessment."""
    model_entries = list(model_match.matched_requirements) if model_match else []
    matched: list[RequirementMatch] = []
    missing: list[str] = []
    numerator = denominator = 0.0
    bands: dict[str, int] = {"strong": 0, "partial": 0, "weak": 0, "none": 0}
    details: list[dict[str, Any]] = []
    for item in evidence:
        req = item.requirement
        factor = KIND_FACTOR.get(req.kind, 1.0) * max(req.weight, 0.0)
        numerator += factor * item.strength
        denominator += factor
        entry = _match_model_entry(req, model_entries)
        blended = item.strength
        if entry is not None:
            blended = 0.5 * item.strength + 0.5 * STRENGTH_VALUE.get(entry.strength, 0.0)
        name = band(blended)
        bands[name] += 1
        evidence_text = (entry.evidence if entry is not None and entry.evidence else None) or _snippet(
            item.best_chunk
        )
        matched.append(RequirementMatch(requirement=req.name[:200], evidence=evidence_text, strength=name))
        if req.kind in MUST_HAVE_KINDS and blended < BANDS[-1][0]:
            missing.append(req.name[:200])
        details.append(
            {
                **item.as_dict(),
                "model_strength": entry.strength if entry else None,
                "blended": round(blended, 3),
            }
        )

    coverage = round(100.0 * numerator / denominator, 1) if denominator > 0 else None
    model_fit = round(float(model_match.overall_fit), 1) if model_match is not None else None
    experience_fit: float | None = None
    if experience_min_years and experience_min_years > 0 and experience_years is not None:
        experience_fit = round(100.0 * min(1.0, experience_years / experience_min_years), 1)

    components = [
        ("coverage", WEIGHT_COVERAGE, coverage),
        ("model", WEIGHT_MODEL, model_fit),
        ("experience", WEIGHT_EXPERIENCE, experience_fit),
    ]
    present = [(name, weight, value) for name, weight, value in components if value is not None]
    total_weight = sum(weight for _, weight, _ in present)
    score = (
        round(sum(weight * value for _, weight, value in present) / total_weight, 1) if total_weight else None
    )
    weights = {name: round(weight / total_weight, 3) for name, weight, _ in present} if total_weight else {}

    if model_match is not None:
        for item in model_match.missing_requirements:
            if item and all(canonical_key(item) != canonical_key(m) for m in missing) and len(missing) < 20:
                missing.append(item[:200])
    # Notes stay human-readable: the model's narrative plus a plain summary of the
    # requirement evidence. The arithmetic (score, components, weights, bands) is
    # the structured MatchBreakdown (stored with the profile, API ``match_breakdown``).
    parts: list[str] = []
    if model_match is not None and model_match.notes:
        parts.append(model_match.notes.strip())
    if evidence:
        parts.append(requirements_summary(bands, len(evidence)))
    if score is None:
        parts.append("No job-match score: no evidence was available.")
    note = " ".join(part for part in parts if part)
    job_match = JobMatch(
        overall_fit=max(0.0, min(100.0, score if score is not None else 0.0)),
        relevant_experience_years=_relevant_years(model_match, experience_years),
        matched_requirements=matched[:40] if matched else (model_entries[:40] if model_match else []),
        missing_requirements=missing[:20],
        strengths=list(model_match.strengths) if model_match else [],
        gaps=list(model_match.gaps) if model_match else [],
        notes=note[:800],
    )
    breakdown = MatchBreakdown(
        score=score,
        coverage=coverage,
        model_fit=model_fit,
        experience_fit=experience_fit,
        weights=weights,
        bands=bands,
        requirements=details,
    )
    return job_match, breakdown


def requirements_summary(bands: dict[str, int], total: int) -> str:
    """One readable sentence about the deterministic requirement evidence."""
    found = [
        f"{bands.get(key, 0)} {label}"
        for key, label in (("strong", "strongly"), ("partial", "partially"), ("weak", "weakly"))
        if bands.get(key, 0)
    ]
    missing = bands.get("none", 0)
    noun = "requirement" if total == 1 else "requirements"
    sentence = f"Resume evidence for the {total} job {noun}: "
    sentence += ", ".join(found) + " supported" if found else "none supported"
    if missing:
        sentence += f", {missing} not found"
    return sentence + "."


def _relevant_years(model_match: JobMatch | None, experience_years: float | None) -> float | None:
    value = model_match.relevant_experience_years if model_match else None
    if value is None:
        return None
    if experience_years is not None:
        value = min(value, experience_years)
    return round(max(0.0, min(60.0, value)), 1)
