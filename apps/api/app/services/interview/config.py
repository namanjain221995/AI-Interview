"""Engine tunables. Platform-wide values (closing reserve, interviewer name)
come from ``app.core.config``; the rest are engine constants collected here
so tests and operators have one place to look."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import get_settings


def _closing_reserve() -> float:
    return float(get_settings().closing_reserve_seconds)


def _interviewer_name() -> str:
    return get_settings().interviewer_name


@dataclass(slots=True)
class EngineConfig:
    closing_reserve_s: float = field(default_factory=_closing_reserve)
    default_interviewer_name: str = field(default_factory=_interviewer_name)
    # start(): wait for session.prepare before falling back to the deterministic plan
    start_wait_for_plan_s: float = 8.0
    # Turn decision streaming
    turn_first_speech_timeout_s: float = 8.0
    # Total wait for the first spoken words across attempts (Tier 1: the old 8 s + 8 s retry left candidates in
    # 16-25 s of silence); a retry is only started while turn_retry_min_s of it remains, else the deterministic
    # fallback question is spoken at once.
    turn_first_speech_budget_s: float = 6.0
    turn_retry_min_s: float = 1.5
    turn_total_timeout_s: float = 35.0
    turn_temperature: float = 0.5
    turn_max_tokens: int = 900
    turn_retry_trimmed: bool = True
    turn_decision_compact: bool = field(default_factory=lambda: get_settings().turn_decision_compact)
    # The metadata tail after the speech is bounded by roughly the speech's play time.
    turn_tail_min_s: float = 2.5
    turn_tail_s_per_word: float = 0.3
    # Preparation / background generations
    # session.prepare runs while the candidate does the system check / waits for the profile, so the
    # plan gets a longer (still bounded) budget; start() waits only start_wait_for_plan_s and falls back.
    plan_timeout_s: float = 70.0
    opening_timeout_s: float = 15.0
    first_question_timeout_s: float = 20.0
    # Wait for the candidate profile while a resume is still processing (full profile ~15-20 s); after the
    # budget the latest usable profile or the quick profile (status "quick") is used, else none.
    profile_wait_budget_s: float = 90.0
    profile_wait_retry_s: float = 10.0
    evaluation_timeout_s: float = 90.0
    evaluation_delay_s: float = 3.0  # answer.evaluate starts after the turn's speech is out (typed answers)
    # Spoken answers: the realtime layer re-transcribes the whole answer in the background after the
    # turn (app/realtime/revision.py, applied only while the answer is unevaluated); waiting a little
    # longer lets the evaluation read the full-context transcript.
    evaluation_delay_spoken_s: float = 9.0
    memory_timeout_s: float = 60.0
    # Speculative preparation. The quick assessment (a main-model call during the end-of-turn
    # pause) is off by default: under shared production load it timed out or was cancelled in
    # 22 of 23 calls while burning GPU; the turn decision assesses the answer itself. Bank
    # retrieval prefetch still runs during the pause.
    quick_assessment_enabled: bool = False
    quick_assessment_timeout_s: float = 4.0
    prep_min_words: int = 6
    prep_min_new_words: int = 3
    prep_ttl_s: int = 600
    # Retrieval inside the turn path (bounded; speculative prefetch fills the cache)
    turn_retrieval_timeout_s: float = 0.8
    bank_candidates: int = 3
    near_duplicate_threshold: float = 0.88
    # Memory
    memory_window_exchanges: int = 8
    memory_summarize_after: int = 12
    conversation_max_entries: int = 40
    utterances_max: int = 16
    # Concurrency / persistence
    lock_timeout_s: float = 20.0
    # Terminal and administrative transitions (pause, resume, end) wait out a turn that legitimately holds the
    # lock (bounded by turn_total_timeout_s) before giving up with EngineLockTimeout.
    strict_lock_timeout_s: float = 45.0
    postgres_flush_interval_s: float = 15.0
    state_ttl_active_s: int = 48 * 3600
    state_ttl_ended_s: int = 24 * 3600
    evals_ttl_s: int = 48 * 3600
    # Silence handling: prompts before moving on
    silence_prompts_before_move_on: int = 2
    # Coding
    coding_followup_min_remaining_s: float = 120.0


_config: EngineConfig | None = None


def get_engine_config() -> EngineConfig:
    global _config
    if _config is None:
        _config = EngineConfig()
    return _config


def set_engine_config(config: EngineConfig | None) -> None:
    global _config
    _config = config


__all__ = ["EngineConfig", "get_engine_config", "set_engine_config"]
