"""Evidence-based scoring (ENGINE_DESIGN.md section 7).

Pure math (no I/O)::

    from app.services.scoring import get_scoring
    scoring = get_scoring()                      # implements contracts.ScoringApi
    obs = scoring.observations_from_evaluation(evaluation, competency_keys=[...], difficulty=..., ...)
    states = scoring.compute_competency_states(round_config, observations, evidence, previous=...)
    rubric = scoring.aggregate_rubric(round_config, states, dimension_averages)
    rec = scoring.recommend(round_config, rubric, states, unreviewed_integrity_flags=0, ...)
    scope = round_scope(round_config)            # evidence budget + targets scaled to the round

Persistence (inside the caller's transaction; never commits)::

    from app.services.scoring import recompute_session_scores
    result = await recompute_session_scores(db, session_id, final=False)   # after an evaluation
    result = await recompute_session_scores(db, session_id, final=True)    # scoring.finalize
"""

from app.services.scoring.compute import (
    RoundScope,
    ScoringService,
    aggregate_rubric,
    assistance_factor,
    compute_competency_states,
    get_scoring,
    observations_from_coding,
    observations_from_evaluation,
    recommend,
    round_scope,
)
from app.services.scoring.contracts import (
    EvidenceRecord,
    Observation,
    RecommendationResult,
    RubricCategoryResult,
    RubricResult,
    ScoringApi,
    ScoringResult,
)
from app.services.scoring.persistence import (
    ScoringComputation,
    compute_session_scores,
    latest_score_rows,
    recompute_session_scores,
)

__all__ = [
    "EvidenceRecord",
    "Observation",
    "RecommendationResult",
    "RubricCategoryResult",
    "RoundScope",
    "RubricResult",
    "ScoringApi",
    "ScoringComputation",
    "ScoringResult",
    "ScoringService",
    "aggregate_rubric",
    "assistance_factor",
    "compute_competency_states",
    "compute_session_scores",
    "get_scoring",
    "latest_score_rows",
    "observations_from_coding",
    "observations_from_evaluation",
    "recommend",
    "recompute_session_scores",
    "round_scope",
]
