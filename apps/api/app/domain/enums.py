"""Shared domain vocabulary.

These enums are the single source of truth for status values, roles and
categorical fields. Database columns store the ``.value`` strings; API and AI
schemas reference the enums directly. The frontend mirrors them in
``apps/web/src/lib/types.ts``.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    RECRUITER = "RECRUITER"
    INTERVIEWER = "INTERVIEWER"
    CANDIDATE = "CANDIDATE"


class SubjectType(StrEnum):
    USER = "user"
    CANDIDATE = "candidate"


class RoundType(StrEnum):
    RESUME_SCREENING = "resume_screening"
    RECRUITER_SCREENING = "recruiter_screening"
    TECHNICAL_SCREENING = "technical_screening"
    DEEP_TECHNICAL = "deep_technical"
    CODING = "coding"
    SYSTEM_DESIGN = "system_design"
    PROBLEM_SOLVING = "problem_solving"
    BEHAVIORAL = "behavioral"
    MANAGERIAL = "managerial"
    LEADERSHIP = "leadership"
    CUSTOM = "custom"
    COMBINED = "combined"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


DIFFICULTY_ORDER: tuple[Difficulty, ...] = (Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD)


class JobStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"
    ARCHIVED = "archived"


class RequirementKind(StrEnum):
    REQUIRED_SKILL = "required_skill"
    PREFERRED_SKILL = "preferred_skill"
    RESPONSIBILITY = "responsibility"
    QUALIFICATION = "qualification"


class CompetencyCategory(StrEnum):
    TECHNICAL = "technical"
    PROBLEM_SOLVING = "problem_solving"
    SYSTEM_DESIGN = "system_design"
    BEHAVIORAL = "behavioral"
    LEADERSHIP = "leadership"
    COMMUNICATION = "communication"
    DOMAIN = "domain"
    CODING = "coding"


class InterviewStatus(StrEnum):
    """Lifecycle of an interview invitation (candidate x job x round)."""

    INVITED = "invited"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SessionStatus(StrEnum):
    """Lifecycle of one live interview session."""

    CREATED = "created"
    PREPARING = "preparing"  # plan + opening being generated
    READY = "ready"  # plan ready, waiting for candidate to start
    LIVE = "live"
    PAUSED = "paused"  # disconnected inside the grace window
    COMPLETING = "completing"  # closing spoken, scoring/report running
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class SessionEndReason(StrEnum):
    SUFFICIENT_EVIDENCE = "sufficient_evidence"
    TIME_LIMIT = "time_limit"
    STOPPING_CONDITION = "stopping_condition"
    CANDIDATE_ENDED = "candidate_ended"
    DISCONNECT_TIMEOUT = "disconnect_timeout"
    POLICY_TERMINATION = "policy_termination"
    TECHNICAL_FAILURE = "technical_failure"
    CANCELLED = "cancelled"


class ResumeStatus(StrEnum):
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    EXTRACTING = "extracting"
    OCR = "ocr"
    ANALYZING = "analyzing"
    READY = "ready"
    FAILED = "failed"
    REJECTED = "rejected"


class ExtractionMethod(StrEnum):
    TEXT = "text"
    OCR = "ocr"
    MIXED = "mixed"


class TurnState(StrEnum):
    """Conversation floor ownership tracked by the realtime runtime."""

    IDLE = "idle"
    AI_SPEAKING = "ai_speaking"
    LISTENING = "listening"
    CANDIDATE_SPEAKING = "candidate_speaking"
    PROCESSING = "processing"
    CODING = "coding"
    PAUSED = "paused"
    ENDED = "ended"


class AvatarState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    ACKNOWLEDGING = "ACKNOWLEDGING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    QUESTIONING = "QUESTIONING"
    ENCOURAGING = "ENCOURAGING"
    TRANSITION = "TRANSITION"
    INTERRUPTED = "INTERRUPTED"
    CLOSING = "CLOSING"


class AvatarExpression(StrEnum):
    NEUTRAL = "neutral"
    WARM = "warm"
    CURIOUS = "curious"
    THOUGHTFUL = "thoughtful"
    ENCOURAGING = "encouraging"
    SERIOUS = "serious"
    PLEASED = "pleased"


class AvatarGesture(StrEnum):
    NONE = "none"
    SMALL_OPEN_PALM = "small_open_palm"
    ONE_HAND_EXPLAIN = "one_hand_explain"
    TWO_HAND_EXPLAIN = "two_hand_explain"
    SMALL_EMPHASIS = "small_emphasis"
    QUESTION_GESTURE = "question_gesture"
    NOD = "nod"
    HEAD_TILT = "head_tilt"
    GOODBYE = "goodbye"


class GestureIntensity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SpeechTone(StrEnum):
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    SUPPORTIVE = "supportive"
    NEUTRAL = "neutral"


class SegmentKind(StrEnum):
    INTRO = "intro"
    RESUME_VALIDATION = "resume_validation"
    TECHNICAL = "technical"
    CODING = "coding"
    SYSTEM_DESIGN = "system_design"
    PROBLEM_SOLVING = "problem_solving"
    BEHAVIORAL = "behavioral"
    LEADERSHIP = "leadership"
    RECRUITER = "recruiter"
    CANDIDATE_QUESTIONS = "candidate_questions"
    CLOSING = "closing"


class TurnAction(StrEnum):
    """What the interviewer does next. Chosen by the turn decision model inside
    the constraints computed by the deterministic policy layer."""

    INTRODUCE = "introduce"
    ASK_QUESTION = "ask_question"
    ASK_FOLLOWUP = "ask_followup"
    REQUEST_SPECIFICITY = "request_specificity"
    CLARIFY_QUESTION = "clarify_question"
    SIMPLIFY = "simplify"
    INCREASE_DIFFICULTY = "increase_difficulty"
    VERIFY_CLAIM = "verify_claim"
    VERIFY_CONTRADICTION = "verify_contradiction"
    CHANGE_TOPIC = "change_topic"
    START_CODING = "start_coding"
    CODING_COMMENT = "coding_comment"
    GIVE_HINT = "give_hint"
    ACKNOWLEDGE_AND_WAIT = "acknowledge_and_wait"
    ANSWER_CANDIDATE_QUESTION = "answer_candidate_question"
    REDIRECT = "redirect"
    INVITE_CANDIDATE_QUESTIONS = "invite_candidate_questions"
    CLOSE_INTERVIEW = "close_interview"


class AnswerQuality(StrEnum):
    STRONG = "strong"
    ADEQUATE = "adequate"
    PARTIAL = "partial"
    VAGUE = "vague"
    INCORRECT = "incorrect"
    NO_ANSWER = "no_answer"
    OFF_TOPIC = "off_topic"
    # Non-answers that change the flow rather than the score:
    CLARIFICATION_REQUEST = "clarification_request"
    CANDIDATE_QUESTION = "candidate_question"
    WANTS_TIME = "wants_time"
    WANTS_HINT = "wants_hint"


ANSWER_QUALITY_VALUE: dict[AnswerQuality, float] = {
    AnswerQuality.STRONG: 0.9,
    AnswerQuality.ADEQUATE: 0.7,
    AnswerQuality.PARTIAL: 0.5,
    AnswerQuality.VAGUE: 0.35,
    AnswerQuality.INCORRECT: 0.15,
    AnswerQuality.NO_ANSWER: 0.0,
    AnswerQuality.OFF_TOPIC: 0.2,
}


class QuestionKind(StrEnum):
    INTRO = "intro"
    TECHNICAL = "technical"
    SCENARIO = "scenario"
    BEHAVIORAL = "behavioral"
    RESUME_CLAIM = "resume_claim"
    SYSTEM_DESIGN = "system_design"
    CODING = "coding"
    RECRUITER = "recruiter"
    LEADERSHIP = "leadership"
    FOLLOWUP = "followup"
    CLOSING = "closing"


class QuestionSource(StrEnum):
    CURATED = "curated"
    GENERATED = "generated"
    FOLLOWUP = "followup"


class BankStatus(StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    RETIRED = "retired"


class EvidenceSignal(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class EvidenceSource(StrEnum):
    ANSWER = "answer"
    CODING = "coding"
    RESUME_CLAIM = "resume_claim"
    HINT_USAGE = "hint_usage"


class ClaimStatus(StrEnum):
    UNVERIFIED = "unverified"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    CONTRADICTED = "contradicted"
    NOT_APPLICABLE = "not_applicable"


class ScoreCategory(StrEnum):
    COMPETENCY = "competency"
    RUBRIC_CATEGORY = "rubric_category"
    OVERALL = "overall"


class Recommendation(StrEnum):
    """Assistive only. A human reviewer always owns the employment decision."""

    STRONG_PROCEED = "strong_proceed"
    PROCEED = "proceed"
    HUMAN_REVIEW = "human_review"
    BORDERLINE_EVIDENCE = "borderline_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ReviewDecisionValue(StrEnum):
    ADVANCE = "advance"
    HOLD = "hold"
    REJECT = "reject"
    NEEDS_MORE_INFORMATION = "needs_more_information"


class ReportType(StrEnum):
    CANDIDATE = "candidate"
    RECRUITER = "recruiter"


class ReportStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class CandidateReportMode(StrEnum):
    IMMEDIATE = "immediate"
    AFTER_REVIEW = "after_review"
    NEVER = "never"


class CodeLanguage(StrEnum):
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    PYTHON = "python"
    JAVA = "java"
    GO = "go"
    CPP = "cpp"
    SQL = "sql"


class RunKind(StrEnum):
    RUN = "run"  # free run with optional stdin
    TEST = "test"  # visible tests
    SUBMIT = "submit"  # all tests including hidden


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    COMPILE_ERROR = "compile_error"
    TIMEOUT = "timeout"
    MEMORY_EXCEEDED = "memory_exceeded"
    REJECTED = "rejected"


class CodingSessionStatus(StrEnum):
    ACTIVE = "active"
    SUBMITTED = "submitted"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class SnapshotTrigger(StrEnum):
    AUTOSAVE = "autosave"
    RUN = "run"
    SUBMIT = "submit"
    LANGUAGE_CHANGE = "language_change"
    HINT = "hint"


class IntegrityEventType(StrEnum):
    # Browser / session signals
    FULLSCREEN_EXITED = "fullscreen_exited"
    FULLSCREEN_DECLINED = "fullscreen_declined"
    TAB_HIDDEN = "tab_hidden"
    FOCUS_LOST = "focus_lost"
    RAPID_FOCUS_SWITCHING = "rapid_focus_switching"
    WINDOW_MINIMIZED = "window_minimized"
    COPY_EVENT = "copy_event"
    PASTE_EVENT = "paste_event"
    LARGE_PASTE_IN_CODING = "large_paste_in_coding"
    PROHIBITED_SHORTCUT = "prohibited_shortcut"
    CONTEXT_MENU = "context_menu"
    PAGE_RELOAD = "page_reload"
    DISCONNECT = "disconnect"
    RECONNECT = "reconnect"
    UNUSUAL_DISCONNECT_PATTERN = "unusual_disconnect_pattern"
    MULTIPLE_DISPLAYS_DETECTED = "multiple_displays_detected"
    # Device signals
    CAMERA_DISABLED = "camera_disabled"
    CAMERA_BLOCKED = "camera_blocked"
    MICROPHONE_DISABLED = "microphone_disabled"
    # Vision signals (camera frames, consent + policy required)
    NO_PERSON_VISIBLE = "no_person_visible"
    MULTIPLE_PERSONS_VISIBLE = "multiple_persons_visible"
    PERSON_LEFT_FRAME = "person_left_frame"
    ANOTHER_PERSON_ENTERED = "another_person_entered"
    POSSIBLE_PHONE_VISIBLE = "possible_phone_visible"
    POSSIBLE_PHOTO_TAKING = "possible_photo_taking"
    LOOKING_AWAY_WITH_OTHER_SIGNALS = "looking_away_with_other_signals"
    # Screen share signals
    SCREEN_SHARE_STARTED = "screen_share_started"
    SCREEN_SHARE_STOPPED = "screen_share_stopped"
    SCREEN_SHARE_NOT_FULL_SCREEN = "screen_share_not_full_screen"
    SCREEN_CONTEXT_CHANGE = "screen_context_change"
    # Face presence (on-device face check in the browser, confirmed on the server)
    FACE_NOT_VISIBLE = "face_not_visible"
    MULTIPLE_FACES_VISIBLE = "multiple_faces_visible"
    STATIC_FACE_IGNORED = "static_face_ignored"
    FACE_CHECK_SUMMARY = "face_check_summary"


class IntegritySource(StrEnum):
    BROWSER = "browser"
    VISION = "vision"
    SCREEN = "screen"
    SERVER = "server"


class IntegritySeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IntegrityAction(StrEnum):
    """Never an automatic guilt decision: at most a flag for a human reviewer,
    or a policy action (pause/terminate) for objective, recruiter-configured
    conditions such as required screen sharing being stopped."""

    LOG_ONLY = "log_only"
    WARN_CANDIDATE = "warn_candidate"
    FLAG_FOR_HUMAN_REVIEW = "flag_for_human_review"
    PAUSE_INTERVIEW = "pause_interview"
    TERMINATE_INTERVIEW = "terminate_interview"


class IntegrityReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    DISMISSED = "dismissed"
    NOTED = "noted"
    CONFIRMED_CONCERN = "confirmed_concern"


class MediaKind(StrEnum):
    RESUME = "resume"
    ANSWER_AUDIO = "answer_audio"
    SESSION_AUDIO = "session_audio"
    VIDEO_SEGMENT = "video_segment"
    SCREEN_SEGMENT = "screen_segment"
    CAMERA_FRAME = "camera_frame"
    SCREEN_FRAME = "screen_frame"
    REPORT_ARTIFACT = "report_artifact"
    # One finalized (concatenated + remuxed) recording part: camera video with
    # both voices, audio only, or the shared screen (``recording.finalize``).
    SESSION_RECORDING = "session_recording"


class ModelTask(StrEnum):
    """Every model call is tagged with a task; the router maps tasks to
    endpoints and the model_calls table records latency/tokens per task."""

    RESUME_EXTRACTION = "resume_extraction"
    PROFILE_ANALYSIS = "profile_analysis"
    JOB_MATCH = "job_match"
    INTERVIEW_PLANNING = "interview_planning"
    OPENING = "opening"
    TURN_DECISION = "turn_decision"
    ANSWER_ASSESSMENT = "answer_assessment"  # fast, turn-critical
    ANSWER_EVALUATION = "answer_evaluation"  # deep, asynchronous
    CODE_OBSERVATION = "code_observation"
    HINT_GENERATION = "hint_generation"
    CODING_EVALUATION = "coding_evaluation"
    QUESTION_GENERATION = "question_generation"
    COMPETENCY_SUMMARY = "competency_summary"
    REPORT_GENERATION = "report_generation"
    MEMORY_SUMMARY = "memory_summary"
    FRAME_ANALYSIS = "frame_analysis"
    SCREEN_ANALYSIS = "screen_analysis"
    ROUTING_CLASSIFICATION = "routing_classification"
    DOCUMENT_CLASSIFICATION = "document_classification"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    STT = "stt"
    OCR = "ocr"
    TTS = "tts"


class ModelCallStatus(StrEnum):
    OK = "ok"
    REPAIRED = "repaired"
    RETRIED = "retried"
    FALLBACK = "fallback"
    ERROR = "error"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"
    CANCELLED = "cancelled"


class JobState(StrEnum):
    """Background job (queue) lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class ActorType(StrEnum):
    USER = "user"
    CANDIDATE = "candidate"
    SYSTEM = "system"
