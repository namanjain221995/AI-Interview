"""Structured outputs exchanged with the local models.

Every schema here is sent to vLLM as ``response_format={"type": "json_schema"}``
(guided decoding) and validated again with Pydantic on the way back, so
malformed output can never reach the interview state. Field order is
significant where noted: guided decoding emits properties in schema order,
which lets the realtime layer start speaking before the whole object exists.

Fairness constraints apply to every schema: no field may describe or infer
emotion, nervousness, personality, honesty, intelligence, race, ethnicity,
religion, disability, age, gender, attractiveness, accent or any other
protected or sensitive trait. Communication is assessed only on the *content*
of what was said.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    AnswerQuality,
    AvatarExpression,
    AvatarGesture,
    AvatarState,
    ClaimStatus,
    Difficulty,
    EvidenceSignal,
    GestureIntensity,
    QuestionKind,
    Recommendation,
    SegmentKind,
    SpeechTone,
    TurnAction,
)


class AIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# =============================================================================
# Resume understanding
# =============================================================================


class SkillItem(AIModel):
    name: str = Field(..., max_length=80)
    category: str = Field(
        "other",
        description="programming_language | framework | database | cloud | devops | ai_ml | tool | architecture | soft | other",
    )
    claimed_level: str = Field("unspecified", description="beginner | intermediate | advanced | expert | unspecified")
    years: float | None = Field(None, ge=0, le=60)
    last_used_year: int | None = None
    evidence: str | None = Field(None, max_length=300, description="Short quote from the resume")


class Achievement(AIModel):
    text: str = Field(..., max_length=400)
    metric: str | None = Field(None, max_length=120, description="e.g. 'cloud cost', 'latency', 'users'")
    value: str | None = Field(None, max_length=60, description="e.g. '40%', '10,000'")
    measurable: bool = False


class RoleEntry(AIModel):
    title: str = Field(..., max_length=160)
    company: str = Field(..., max_length=160)
    start: str | None = Field(None, description="YYYY-MM or YYYY")
    end: str | None = Field(None, description="YYYY-MM, YYYY or 'present'")
    duration_months: int | None = Field(None, ge=0, le=720)
    location: str | None = None
    responsibilities: list[str] = Field(default_factory=list, max_length=15)
    achievements: list[Achievement] = Field(default_factory=list, max_length=12)
    technologies: list[str] = Field(default_factory=list, max_length=40)
    leadership_scope: str | None = Field(None, max_length=200, description="e.g. 'managed 6 engineers'")


class ProjectEntry(AIModel):
    name: str = Field(..., max_length=160)
    description: str = Field("", max_length=600)
    role: str | None = Field(None, max_length=160)
    technologies: list[str] = Field(default_factory=list, max_length=30)
    outcomes: list[str] = Field(default_factory=list, max_length=8)
    scale_indicators: list[str] = Field(default_factory=list, max_length=8, description="users, rps, data volume, nodes")


class EducationEntry(AIModel):
    institution: str = Field(..., max_length=200)
    degree: str | None = Field(None, max_length=160)
    field: str | None = Field(None, max_length=160)
    start: str | None = None
    end: str | None = None


class CertificationEntry(AIModel):
    name: str = Field(..., max_length=200)
    issuer: str | None = Field(None, max_length=120)
    year: str | None = None


class Inconsistency(AIModel):
    description: str = Field(..., max_length=400)
    severity: str = Field("low", description="low | medium | high")


class ResumeExtraction(AIModel):
    """Factual extraction only — what the resume states, nothing inferred."""

    candidate_name: str | None = Field(None, max_length=160)
    headline: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=1200)
    total_experience_years: float | None = Field(None, ge=0, le=60)
    roles: list[RoleEntry] = Field(default_factory=list, max_length=25)
    projects: list[ProjectEntry] = Field(default_factory=list, max_length=25)
    education: list[EducationEntry] = Field(default_factory=list, max_length=10)
    certifications: list[CertificationEntry] = Field(default_factory=list, max_length=20)
    skills: list[SkillItem] = Field(default_factory=list, max_length=120)
    leadership_experience: list[str] = Field(default_factory=list, max_length=10)
    career_progression: str | None = Field(None, max_length=600)
    inconsistencies: list[Inconsistency] = Field(default_factory=list, max_length=10)


class ClaimToVerify(AIModel):
    """A resume statement the interview should gather evidence about.

    Resume statements are never assumed true."""

    id: str = Field(..., max_length=24, description="Stable id such as 'c1'")
    claim: str = Field(..., max_length=400)
    source_quote: str | None = Field(None, max_length=400)
    category: str = Field("technical", description="technical | scale | impact | leadership | ownership | tenure | other")
    competency_keys: list[str] = Field(default_factory=list, max_length=5)
    evidence_topics: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="What a credible answer must cover, e.g. node count, autoscaling, ingress, HA",
    )
    priority: int = Field(3, ge=1, le=5, description="5 = most important to verify for this job")


class RequirementMatch(AIModel):
    requirement: str = Field(..., max_length=200)
    evidence: str | None = Field(None, max_length=300)
    strength: str = Field("none", description="strong | partial | weak | none")


class JobMatch(AIModel):
    overall_fit: float = Field(..., ge=0, le=100)
    relevant_experience_years: float | None = Field(None, ge=0, le=60)
    matched_requirements: list[RequirementMatch] = Field(default_factory=list, max_length=40)
    missing_requirements: list[str] = Field(default_factory=list, max_length=20)
    strengths: list[str] = Field(default_factory=list, max_length=10)
    gaps: list[str] = Field(default_factory=list, max_length=10)
    notes: str | None = Field(None, max_length=800)


class RecommendedTopic(AIModel):
    topic: str = Field(..., max_length=120)
    competency_key: str | None = Field(None, max_length=80)
    reason: str = Field(..., max_length=300)
    priority: int = Field(3, ge=1, le=5)


class ProfileAnalysis(AIModel):
    """Interview-oriented analysis of an extraction against a job."""

    claims_to_verify: list[ClaimToVerify] = Field(default_factory=list, max_length=15)
    job_match: JobMatch
    recommended_topics: list[RecommendedTopic] = Field(default_factory=list, max_length=15)
    potential_skill_gaps: list[str] = Field(default_factory=list, max_length=15)
    areas_requiring_verification: list[str] = Field(default_factory=list, max_length=10)
    job_relevant_strengths: list[str] = Field(default_factory=list, max_length=10)
    job_relevant_missing_areas: list[str] = Field(default_factory=list, max_length=10)


class CandidateProfile(AIModel):
    """Normalized candidate profile stored in candidate_profiles.profile."""

    candidate_id: str
    candidate_name: str | None = None
    headline: str | None = None
    summary: str | None = None
    experience_years: float | None = None
    relevant_experience_years: float | None = None
    roles: list[RoleEntry] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    skills: dict[str, list[SkillItem]] = Field(default_factory=dict, description="Grouped by category")
    projects: list[ProjectEntry] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    leadership_experience: list[str] = Field(default_factory=list)
    career_progression: str | None = None
    inconsistencies: list[Inconsistency] = Field(default_factory=list)
    claims_to_verify: list[ClaimToVerify] = Field(default_factory=list)
    job_match: JobMatch | None = None
    recommended_topics: list[RecommendedTopic] = Field(default_factory=list)
    potential_skill_gaps: list[str] = Field(default_factory=list)
    areas_requiring_verification: list[str] = Field(default_factory=list)
    job_relevant_strengths: list[str] = Field(default_factory=list)
    job_relevant_missing_areas: list[str] = Field(default_factory=list)


# =============================================================================
# Planning
# =============================================================================


class PlanSegment(AIModel):
    key: str = Field(..., max_length=40)
    kind: SegmentKind
    competency_keys: list[str] = Field(default_factory=list, max_length=10)
    time_budget_minutes: float = Field(..., ge=0.5, le=120)
    goals: list[str] = Field(default_factory=list, max_length=6)


class CompetencyTarget(AIModel):
    key: str = Field(..., max_length=80)
    priority: int = Field(3, ge=1, le=5)
    starting_difficulty: Difficulty = Difficulty.MEDIUM
    focus_areas: list[str] = Field(default_factory=list, max_length=8)
    rationale: str | None = Field(None, max_length=300)


class InterviewPlan(AIModel):
    """Adaptive plan: a budget and priorities, not a fixed question list."""

    opening_focus: str = Field(..., max_length=300, description="What the first substantive question explores")
    segments: list[PlanSegment] = Field(..., min_length=1, max_length=10)
    competency_targets: list[CompetencyTarget] = Field(default_factory=list, max_length=20)
    claims_priority: list[str] = Field(default_factory=list, max_length=15, description="Claim ids in verification order")
    initial_difficulty: Difficulty = Difficulty.MEDIUM
    difficulty_strategy: str = Field("", max_length=400)
    coding_plan: str | None = Field(None, max_length=300)
    risks_to_probe: list[str] = Field(default_factory=list, max_length=8)


# =============================================================================
# Live turn decisions
# =============================================================================


class AvatarBehavior(AIModel):
    """Semantic behaviour only. Bones, blendshapes, blinking and breathing are
    driven deterministically by the browser behaviour engine."""

    state: AvatarState = AvatarState.SPEAKING
    expression: AvatarExpression = AvatarExpression.NEUTRAL
    gesture: AvatarGesture = AvatarGesture.NONE
    gesture_intensity: GestureIntensity = GestureIntensity.LOW
    tone: SpeechTone = SpeechTone.PROFESSIONAL


class TurnDecision(AIModel):
    """Next interviewer move.

    FIELD ORDER IS A CONTRACT. ``speech`` comes first so the realtime layer can
    stream it to TTS after ~5 tokens (measured on this deployment: guided-JSON
    headers cost 1.3-2.0 s before the first spoken word). The move itself is
    decided *before* this call by the deterministic policy layer (allowed
    actions, target competency, difficulty guidance, quick assessment of the
    answer) and passed in the prompt; the fields after ``speech`` report what
    the model did and are validated when the stream completes. Every field is
    required (nullable where it may not apply) so guided decoding always emits
    the full record.
    """

    speech: str = Field(
        ...,
        min_length=1,
        max_length=700,
        description="Exactly what the interviewer says: conversational, 1-3 short sentences, ONE question, no markdown",
    )
    action: TurnAction
    answer_quality: AnswerQuality | None = Field(
        ..., description="Assessment of the candidate's last answer (null for the opening question)"
    )
    competency_key: str | None = Field(..., max_length=80)
    topic: str | None = Field(..., max_length=120)
    difficulty: Difficulty
    kind: QuestionKind
    expression: AvatarExpression
    gesture: AvatarGesture
    gesture_intensity: GestureIntensity
    question_focus: str | None = Field(..., max_length=200, description="Canonical short form of the question asked")
    expected_evidence: list[str] = Field(..., max_length=8)
    claim_id: str | None = Field(..., max_length=24)
    bank_question_id: str | None = Field(..., max_length=40)
    missing_specifics: list[str] = Field(..., max_length=6, description="What the last answer lacked")
    new_claims: list[str] = Field(..., max_length=4, description="Interesting claims made in the last answer")
    possible_contradiction: str | None = Field(..., max_length=300)
    reason: str = Field(..., max_length=400, description="Why this move; stored for auditability")

    def avatar_behavior(self) -> AvatarBehavior:
        """Compose the semantic avatar behaviour sent to the browser."""
        return AvatarBehavior(
            state=ACTION_AVATAR_STATE.get(self.action, AvatarState.SPEAKING),
            expression=self.expression,
            gesture=self.gesture,
            gesture_intensity=self.gesture_intensity,
            tone=SpeechTone.SUPPORTIVE if self.action in _SUPPORTIVE_ACTIONS else SpeechTone.PROFESSIONAL,
        )


ACTION_AVATAR_STATE: dict[TurnAction, AvatarState] = {
    TurnAction.INTRODUCE: AvatarState.SPEAKING,
    TurnAction.ASK_QUESTION: AvatarState.QUESTIONING,
    TurnAction.ASK_FOLLOWUP: AvatarState.QUESTIONING,
    TurnAction.REQUEST_SPECIFICITY: AvatarState.QUESTIONING,
    TurnAction.CLARIFY_QUESTION: AvatarState.SPEAKING,
    TurnAction.SIMPLIFY: AvatarState.ENCOURAGING,
    TurnAction.INCREASE_DIFFICULTY: AvatarState.QUESTIONING,
    TurnAction.VERIFY_CLAIM: AvatarState.QUESTIONING,
    TurnAction.VERIFY_CONTRADICTION: AvatarState.QUESTIONING,
    TurnAction.CHANGE_TOPIC: AvatarState.TRANSITION,
    TurnAction.START_CODING: AvatarState.TRANSITION,
    TurnAction.CODING_COMMENT: AvatarState.SPEAKING,
    TurnAction.GIVE_HINT: AvatarState.ENCOURAGING,
    TurnAction.ACKNOWLEDGE_AND_WAIT: AvatarState.ACKNOWLEDGING,
    TurnAction.ANSWER_CANDIDATE_QUESTION: AvatarState.SPEAKING,
    TurnAction.REDIRECT: AvatarState.SPEAKING,
    TurnAction.INVITE_CANDIDATE_QUESTIONS: AvatarState.SPEAKING,
    TurnAction.CLOSE_INTERVIEW: AvatarState.CLOSING,
}

_SUPPORTIVE_ACTIONS = {TurnAction.SIMPLIFY, TurnAction.GIVE_HINT, TurnAction.ACKNOWLEDGE_AND_WAIT}


class AnswerAssessment(AIModel):
    """Fast, turn-critical classification of a candidate turn (used when the
    turn decision needs a separate pre-classification, e.g. barge-in
    backchannel detection or coding voice commands)."""

    quality: AnswerQuality
    confidence: float = Field(..., ge=0, le=1)
    is_backchannel: bool = Field(False, description="True for 'mm-hmm', 'yeah', 'okay' with no content")
    topics_detected: list[str] = Field(default_factory=list, max_length=6)
    missing_specifics: list[str] = Field(default_factory=list, max_length=6)
    has_new_claim: bool = False
    possible_contradiction: bool = False


# =============================================================================
# Deep answer evaluation (asynchronous)
# =============================================================================


class DimensionScores(AIModel):
    """0-10 per dimension; null when the dimension does not apply."""

    correctness: float | None = Field(None, ge=0, le=10)
    relevance: float | None = Field(None, ge=0, le=10)
    completeness: float | None = Field(None, ge=0, le=10)
    depth: float | None = Field(None, ge=0, le=10)
    specificity: float | None = Field(None, ge=0, le=10)
    practical_experience: float | None = Field(None, ge=0, le=10)
    reasoning: float | None = Field(None, ge=0, le=10)
    consistency: float | None = Field(None, ge=0, le=10)
    architecture_awareness: float | None = Field(None, ge=0, le=10)
    tradeoff_understanding: float | None = Field(None, ge=0, le=10)
    communication_clarity: float | None = Field(None, ge=0, le=10, description="Clarity of content only")


class EvidenceItem(AIModel):
    competency_key: str = Field(..., max_length=80)
    dimension: str = Field(..., max_length=80, description="e.g. availability, cost_measurement, debugging")
    signal: EvidenceSignal
    evidence: str = Field(..., max_length=500, description="Specific, attributable observation")
    quote: str | None = Field(None, max_length=300, description="Verbatim supporting excerpt from the answer")
    strength: float = Field(0.5, ge=0, le=1)
    confidence: float = Field(0.6, ge=0, le=1)


class ContradictionFinding(AIModel):
    statement: str = Field(..., max_length=300)
    conflicts_with: str = Field(..., max_length=300, description="Earlier answer or resume statement")
    severity: str = Field("low", description="low | medium | high")


class ClaimVerification(AIModel):
    claim_id: str = Field(..., max_length=24)
    status: ClaimStatus
    note: str = Field("", max_length=300)


class SuggestedFollowup(AIModel):
    text: str = Field(..., max_length=300)
    reason: str = Field(..., max_length=200)
    priority: int = Field(3, ge=1, le=5)


class AnswerEvaluation(AIModel):
    overall_quality: AnswerQuality
    scores: DimensionScores
    summary: str = Field(..., max_length=600)
    technical_evidence: list[str] = Field(default_factory=list, max_length=8)
    measurable_outcomes: list[str] = Field(default_factory=list, max_length=6)
    missing_details: list[str] = Field(default_factory=list, max_length=8)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=6)
    contradictions: list[ContradictionFinding] = Field(default_factory=list, max_length=4)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=12)
    claim_verifications: list[ClaimVerification] = Field(default_factory=list, max_length=6)
    suggested_followups: list[SuggestedFollowup] = Field(default_factory=list, max_length=4)
    evaluation_confidence: float = Field(..., ge=0, le=1)


# =============================================================================
# Coding
# =============================================================================


class CodeObservation(AIModel):
    """Whether (and what) to say while the candidate codes. Silence is the
    default; interrupting a focused candidate is a cost."""

    should_speak: bool
    kind: str = Field("none", description="question | nudge | encouragement | time_warning | clarification | none")
    focus: str = Field("other", description="complexity | edge_cases | bug | approach | testing | readability | other")
    behavior: AvatarBehavior = Field(default_factory=AvatarBehavior)
    speech: str = Field("", max_length=400)
    reason: str = Field("", max_length=300)


class HintResponse(AIModel):
    level: int = Field(..., ge=1, le=3)
    hint_text: str = Field(..., max_length=500, description="Displayed in the hint panel")
    speech: str = Field(..., max_length=400, description="Spoken version")
    concept_tags: list[str] = Field(default_factory=list, max_length=5)


class CodingEvaluation(AIModel):
    correctness_summary: str = Field(..., max_length=500)
    tests_passed: int = Field(0, ge=0)
    tests_total: int = Field(0, ge=0)
    time_complexity: str | None = Field(None, max_length=60)
    space_complexity: str | None = Field(None, max_length=60)
    complexity_assessment: str = Field("", max_length=400)
    code_quality: float = Field(..., ge=0, le=10)
    problem_solving: float = Field(..., ge=0, le=10)
    edge_case_handling: float = Field(..., ge=0, le=10)
    testing_approach: float | None = Field(None, ge=0, le=10)
    communication: float | None = Field(None, ge=0, le=10, description="Content of explanations only")
    process_observations: list[str] = Field(default_factory=list, max_length=8)
    hints_impact: str = Field("", max_length=300)
    strengths: list[str] = Field(default_factory=list, max_length=6)
    weaknesses: list[str] = Field(default_factory=list, max_length=6)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=10)
    score_estimate: float = Field(..., ge=0, le=10)
    confidence: float = Field(..., ge=0, le=1)


# =============================================================================
# Question generation
# =============================================================================


class GeneratedQuestion(AIModel):
    competency_key: str = Field(..., max_length=80)
    topic: str = Field(..., max_length=120)
    difficulty: Difficulty
    kind: QuestionKind
    text: str = Field(..., max_length=600)
    expected_concepts: list[str] = Field(default_factory=list, max_length=10)
    rubric_excellent: str = Field("", max_length=400)
    rubric_weak: str = Field("", max_length=400)
    followups: list[str] = Field(default_factory=list, max_length=5)
    common_errors: list[str] = Field(default_factory=list, max_length=5)


class GeneratedQuestionSet(AIModel):
    questions: list[GeneratedQuestion] = Field(..., min_length=1, max_length=20)


# =============================================================================
# Integrity (vision) — objective observations only
# =============================================================================


class FrameObservation(AIModel):
    """Objective scene description of one webcam frame. Never identity,
    emotion, demographics or appearance."""

    person_count: int = Field(..., ge=0, le=10)
    primary_face_visible: bool
    camera_obstructed: bool = Field(False, description="Lens covered or view blocked")
    image_too_dark: bool = False
    phone_like_object_visible: bool = False
    phone_confidence: float = Field(0.0, ge=0, le=1)
    phone_held_toward_screen: bool = Field(False, description="Possible photo-taking posture")
    additional_person_partially_visible: bool = False
    primary_person_looking_away: bool = Field(False, description="Recorded but never acted on alone")
    notes: str = Field("", max_length=200, description="Objective notes only")
    confidence: float = Field(..., ge=0, le=1)


class DetectedFace(AIModel):
    """One face found by the deterministic server-side face detector (OpenCV
    YuNet) in a camera frame: where it is and how sure the detector is. Never
    identity, landmarks, crops or any attribute of the person."""

    box: list[float] = Field(
        ..., min_length=4, max_length=4, description="x, y, width, height as fractions of the frame (0-1)"
    )
    score: float = Field(..., ge=0, le=1)
    static: bool = Field(
        False, description="Motionless across frames (a photo, poster or screen); not counted as a person"
    )
    moving: bool = Field(False, description="Seen moving between frames, so not a picture")


class FaceCheckObservation(AIModel):
    """Deterministic face count for one camera frame (not a model guess about the
    person). ``counted_faces`` excludes static faces; ``live_faces`` counts only
    faces already seen moving between frames."""

    detector: str = Field("yunet-2023mar", max_length=40)
    face_count: int = Field(..., ge=0, le=50)
    counted_faces: int = Field(..., ge=0, le=50)
    static_faces: int = Field(0, ge=0, le=50)
    live_faces: int = Field(0, ge=0, le=50)
    faces: list[DetectedFace] = Field(default_factory=list, max_length=20)
    latency_ms: float = Field(0.0, ge=0)


class ScreenObservation(AIModel):
    active_context: str = Field(..., max_length=120, description="e.g. 'interview tab', 'code editor', 'chat application'")
    interview_window_visible: bool
    other_applications_visible: list[str] = Field(default_factory=list, max_length=6)
    possible_prohibited_context: bool = False
    description: str = Field("", max_length=300)
    confidence: float = Field(..., ge=0, le=1)


# =============================================================================
# Reporting
# =============================================================================


class EvidenceBackedPoint(AIModel):
    title: str = Field(..., max_length=120)
    detail: str = Field(..., max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    competency_key: str | None = Field(None, max_length=80)


class ImprovementArea(AIModel):
    title: str = Field(..., max_length=120)
    detail: str = Field(..., max_length=600, description="What was missing, concretely")
    learning_guidance: str = Field(..., max_length=600, description="Specific next steps to improve")
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    competency_key: str | None = Field(None, max_length=80)


class QuestionHighlight(AIModel):
    question_id: str = Field(..., max_length=40)
    note: str = Field(..., max_length=400)


class StudyArea(AIModel):
    topic: str = Field(..., max_length=120)
    why: str = Field(..., max_length=300)
    next_steps: list[str] = Field(default_factory=list, max_length=5)


class CandidateReportNarrative(AIModel):
    overall_summary: str = Field(..., max_length=1200)
    technical_strengths: list[EvidenceBackedPoint] = Field(default_factory=list, max_length=8)
    areas_to_improve: list[ImprovementArea] = Field(default_factory=list, max_length=8)
    coding_feedback: str | None = Field(None, max_length=800)
    problem_solving_feedback: str | None = Field(None, max_length=800)
    communication_feedback: str | None = Field(None, max_length=600, description="Content-based only")
    strong_answers: list[QuestionHighlight] = Field(default_factory=list, max_length=6)
    incomplete_answers: list[QuestionHighlight] = Field(default_factory=list, max_length=6)
    recommended_study_areas: list[StudyArea] = Field(default_factory=list, max_length=8)
    next_steps: list[str] = Field(default_factory=list, max_length=6)


class CompetencyFinding(AIModel):
    competency_key: str = Field(..., max_length=80)
    summary: str = Field(..., max_length=600)
    strengths: list[str] = Field(default_factory=list, max_length=5)
    weaknesses: list[str] = Field(default_factory=list, max_length=5)
    unanswered_areas: list[str] = Field(default_factory=list, max_length=5)
    evidence_ids: list[str] = Field(default_factory=list, max_length=10)


class RecruiterReportNarrative(AIModel):
    executive_summary: str = Field(..., max_length=1500)
    competency_findings: list[CompetencyFinding] = Field(default_factory=list, max_length=20)
    claims_summary: str = Field("", max_length=800)
    contradictions_summary: str = Field("", max_length=600)
    coding_summary: str | None = Field(None, max_length=800)
    areas_for_human_followup: list[str] = Field(default_factory=list, max_length=8)
    recommendation_rationale: str = Field(..., max_length=800)
    suggested_recommendation: Recommendation
    risks: list[str] = Field(default_factory=list, max_length=6)


class MemorySummary(AIModel):
    """Rolling compression of older conversation for prompt budgets."""

    summary: str = Field(..., max_length=1500)
    key_facts: list[str] = Field(default_factory=list, max_length=12)
