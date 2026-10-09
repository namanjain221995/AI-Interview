"""CodingService against Postgres with a fake sandbox and the mock AI:
start, snapshots, runs (with hidden-test redaction), hints, finish, context,
reconnect payloads and observer state persistence."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.ai.base import AITimeout
from app.core.errors import Conflict
from app.db.models import (
    BackgroundJob,
    CodeRun,
    CodeSnapshot,
    CodingHint,
    CodingQuestion,
    CodingSession,
    CompetencyEvidence,
    Transcript,
)
from app.db.session import session_scope
from app.domain.enums import CodeLanguage, CodingSessionStatus, ModelTask, RunKind, RunStatus
from app.schemas.ai import AvatarBehavior, CodeObservation, HintResponse
from app.services.coding.contracts import CodingService
from app.services.coding.sandbox_client import SANDBOX_BUSY_MESSAGE, SandboxFault
from app.services.coding.service import CodingServiceImpl

from .conftest import SECRET_MARK, create_session, round_config

pytestmark = pytest.mark.integration

PASS_VISIBLE = "def two_sum(nums, target):\n    # PASS_VISIBLE\n    for i in range(len(nums)):\n        for j in range(i + 1, len(nums)):\n            if nums[i] + nums[j] == target:\n                return [i, j]\n    return []\n"
HASHMAP = "def two_sum(nums, target):\n    seen = {}\n    for i, v in enumerate(nums):\n        if target - v in seen:\n            return [seen[target - v], i]\n        seen[v] = i\n    return []\n"


class Clock:
    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


async def start_two_sum(service: CodingServiceImpl, env, language=CodeLanguage.PYTHON):
    return await service.start(
        str(env.session_id), str(env.questions["two_sum"]), question_id=None, language=language
    )


async def count(model, **where) -> int:
    async with session_scope() as db:
        stmt = select(func.count()).select_from(model)
        for key, value in where.items():
            stmt = stmt.where(getattr(model, key) == value)
        return int(await db.scalar(stmt) or 0)


def test_implements_the_protocol(service) -> None:
    assert isinstance(service, CodingService)


async def test_start_creates_session_initial_snapshot_and_candidate_payload(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    payload = state.model_dump_json()
    assert state.language == CodeLanguage.PYTHON
    assert state.code and "def two_sum" in state.code
    assert state.time_limit_s == 20 * 60
    assert state.hints_available == 3 and state.hints_used == 0
    assert state.question.languages == [CodeLanguage.PYTHON, CodeLanguage.JAVASCRIPT, CodeLanguage.GO]
    assert set(state.question.starter_code) == {"python", "javascript", "go"}
    assert [t["id"] for t in state.question.visible_tests] == ["t1", "t2", "t3"]
    for hidden_id in ("t4", "t5", "t10"):
        assert f'"{hidden_id}"' not in payload
    assert "complement" not in payload and "Minimum size input" not in payload
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(state.coding_session_id))
        assert cs.status == "active" and cs.language == "python" and cs.hints_available == 3
        snaps = (await db.scalars(select(CodeSnapshot).where(CodeSnapshot.coding_session_id == cs.id))).all()
        assert [s.seq for s in snaps] == [1] and snaps[0].code == state.code
    again = await start_two_sum(service, coding_env)
    assert again.coding_session_id == state.coding_session_id
    with pytest.raises(Conflict):
        await service.start(
            str(coding_env.session_id),
            str(coding_env.questions["valid_parentheses"]),
            question_id=None,
            language=CodeLanguage.PYTHON,
        )


async def test_start_uses_an_allowed_language(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env, language=CodeLanguage.JAVA)
    assert state.language == CodeLanguage.PYTHON  # java is not allowed by the round


async def test_record_update_snapshots_only_significant_changes(fake_sandbox, mock_ai, coding_env) -> None:
    clock = Clock()
    service = CodingServiceImpl(sandbox=fake_sandbox, ai=mock_ai, clock=clock)
    state = await start_two_sum(service, coding_env)
    cs_id = state.coding_session_id
    base = state.code
    clock.advance(2)
    assert (
        await service.record_update(
            cs_id, code=base.replace("return []", "return [0]"), language=CodeLanguage.PYTHON, version=1
        )
        is False
    )
    clock.advance(2)
    assert await service.record_update(cs_id, code=HASHMAP, language=CodeLanguage.PYTHON, version=2) is True
    assert (
        await service.record_update(
            cs_id, code=HASHMAP + "# stale\n" * 5, language=CodeLanguage.PYTHON, version=2
        )
        is False
    )  # stale version
    clock.advance(5)
    small = HASHMAP.replace("seen = {}", "seen = {}  ")
    assert await service.record_update(cs_id, code=small, language=CodeLanguage.PYTHON, version=3) is False
    clock.advance(21)  # 20 s since the last snapshot with a pending change
    tiny = small.replace("return []", "return [ ]")
    assert await service.record_update(cs_id, code=tiny, language=CodeLanguage.PYTHON, version=4) is True
    async with session_scope() as db:
        snaps = (
            await db.scalars(
                select(CodeSnapshot)
                .where(CodeSnapshot.coding_session_id == uuid.UUID(cs_id))
                .order_by(CodeSnapshot.seq)
            )
        ).all()
    assert [s.seq for s in snaps] == [1, 2, 3]
    second = snaps[1]
    assert second.trigger == "autosave" and second.code == HASHMAP
    assert second.diff_from_previous.startswith("--- v1\n+++ v2")
    assert second.lines_added >= 4 and second.lines_removed >= 1
    assert second.char_count == len(HASHMAP)
    assert second.elapsed_seconds == pytest.approx(4, abs=0.5)
    assert snaps[2].code == tiny


async def test_language_change_snapshots_and_rejects_disallowed_languages(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    go_code = state.question.starter_code["go"]
    assert (
        await service.record_update(
            state.coding_session_id, code=go_code, language=CodeLanguage.GO, version=1
        )
        is True
    )
    assert (
        await service.record_update(
            state.coding_session_id, code="class Solution {}", language=CodeLanguage.JAVA, version=2
        )
        is False
    )
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(state.coding_session_id))
        last = (
            await db.scalars(
                select(CodeSnapshot)
                .where(CodeSnapshot.coding_session_id == cs.id)
                .order_by(CodeSnapshot.seq.desc())
            )
        ).first()
    assert cs.language == "go"
    assert last.trigger == "language_change" and last.language == "go"


async def test_test_runs_use_visible_tests_and_submit_runs_all_with_hidden_details_redacted(
    service, coding_env, fake_sandbox
) -> None:
    state = await start_two_sum(service, coding_env)
    cs_id = state.coding_session_id
    test_result = await service.run(cs_id, kind=RunKind.TEST, code=PASS_VISIBLE, language=CodeLanguage.PYTHON)
    assert test_result.status == RunStatus.PASSED
    assert (test_result.passed, test_result.total) == (3, 3)
    assert all(not t.hidden for t in fake_sandbox.requests[-1].tests)

    submit = await service.run(cs_id, kind=RunKind.SUBMIT, code=PASS_VISIBLE, language=CodeLanguage.PYTHON)
    assert submit.status == RunStatus.FAILED
    assert submit.passed == 3 and submit.total == 10
    assert len(fake_sandbox.requests[-1].tests) == 10
    message = submit.model_dump_json()
    assert SECRET_MARK not in message
    assert [t["id"] for t in submit.tests] == ["t1", "t2", "t3"]
    assert all("args" in t and "expected" in t and t["passed"] for t in submit.tests)
    for hidden_id in ("t4", "t9", "t10"):
        assert f'"{hidden_id}"' not in message

    async with session_scope() as db:
        runs = (
            await db.scalars(
                select(CodeRun)
                .where(CodeRun.coding_session_id == uuid.UUID(cs_id))
                .order_by(CodeRun.created_at)
            )
        ).all()
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
        snaps = (
            await db.scalars(
                select(CodeSnapshot).where(CodeSnapshot.coding_session_id == cs.id).order_by(CodeSnapshot.seq)
            )
        ).all()
    assert [r.kind for r in runs] == ["test", "submit"]
    staff = runs[1].test_results
    hidden = [t for t in staff if t["hidden"]]
    assert len(hidden) == 7 and all(SECRET_MARK in str(t["actual"]) for t in hidden)
    assert all("args" in t and "description" in t for t in hidden)
    assert runs[1].sandbox_meta["request_id"] == runs[1].id.hex
    assert cs.final_run_id == runs[1].id and cs.final_code == PASS_VISIBLE
    # The run snapshot was taken once (identical code for the second run).
    assert [s.trigger for s in snaps] == ["autosave", "run"]
    assert runs[0].snapshot_id == runs[1].snapshot_id == snaps[1].id


async def test_runtime_errors_show_visible_errors_only(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    result = await service.run(
        state.coding_session_id, kind=RunKind.SUBMIT, code="# RUNTIME_ERROR\n", language=CodeLanguage.PYTHON
    )
    assert result.status == RunStatus.ERROR
    assert SECRET_MARK not in result.model_dump_json()
    assert all(t["error"] == "IndexError: list index out of range" for t in result.tests)


async def test_free_run_mode_returns_stdout(service, coding_env, fake_sandbox) -> None:
    state = await start_two_sum(service, coding_env)
    result = await service.run(
        state.coding_session_id,
        kind=RunKind.RUN,
        code="print(input())",
        language=CodeLanguage.PYTHON,
        stdin="hello",
    )
    assert result.status == RunStatus.PASSED and result.stdout == "ran:hello"
    assert (result.passed, result.total, result.tests) == (0, 0, [])
    assert fake_sandbox.requests[-1].mode == "run"


async def test_platform_fault_maps_to_candidate_safe_error(service, coding_env, fake_sandbox) -> None:
    state = await start_two_sum(service, coding_env)
    fake_sandbox.fail_next.append(
        SandboxFault("docker socket error at /var/run/docker.sock", reason="docker_error", status_code=503)
    )
    result = await service.run(
        state.coding_session_id, kind=RunKind.TEST, code=PASS_VISIBLE, language=CodeLanguage.PYTHON
    )
    assert result.status == RunStatus.ERROR
    assert result.stderr == SANDBOX_BUSY_MESSAGE
    assert "docker" not in result.model_dump_json()
    async with session_scope() as db:
        run = (
            await db.scalars(
                select(CodeRun).where(CodeRun.coding_session_id == uuid.UUID(state.coding_session_id))
            )
        ).one()
        cs = await db.get(CodingSession, uuid.UUID(state.coding_session_id))
    assert (
        run.status == "error"
        and run.sandbox_meta["platform_fault"] is True
        and run.sandbox_meta["reason"] == "docker_error"
    )
    assert cs.observer_state["runs"][-1]["platform_fault"] is True


async def test_runs_are_rejected_for_disallowed_languages_and_rapid_repeats(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    rejected = await service.run(
        state.coding_session_id, kind=RunKind.TEST, code="class Solution {}", language=CodeLanguage.JAVA
    )
    assert rejected.status == RunStatus.REJECTED and "java" in rejected.stderr
    await service.run(
        state.coding_session_id, kind=RunKind.TEST, code=PASS_VISIBLE, language=CodeLanguage.PYTHON
    )
    again = await service.run(
        state.coding_session_id, kind=RunKind.TEST, code=PASS_VISIBLE, language=CodeLanguage.PYTHON
    )
    assert again.status == RunStatus.REJECTED  # within MIN_RUN_INTERVAL_S


async def test_exactly_three_curated_hints_in_order_then_none(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    cs_id = state.coding_session_id
    async with session_scope() as db:
        curated = (await db.get(CodingQuestion, coding_env.questions["two_sum"])).hints
    issued = []
    for expected_level in (1, 2, 3):
        result = await service.request_hint_detailed(
            cs_id, requested_via="button" if expected_level != 2 else "voice"
        )
        assert result is not None and result.level == expected_level and result.source == "curated"
        assert result.text == curated[expected_level - 1]
        assert result.hints_used == expected_level and result.hints_remaining == 3 - expected_level
        issued.append(result.text)
    assert await service.request_hint(cs_id, requested_via="button") is None
    assert await service.request_hint(cs_id, requested_via="voice") is None
    async with session_scope() as db:
        hints = (
            await db.scalars(
                select(CodingHint)
                .where(CodingHint.coding_session_id == uuid.UUID(cs_id))
                .order_by(CodingHint.level)
            )
        ).all()
        evidence = (
            await db.scalars(
                select(CompetencyEvidence).where(CompetencyEvidence.coding_session_id == uuid.UUID(cs_id))
            )
        ).all()
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
    assert [h.level for h in hints] == [1, 2, 3]
    assert [h.requested_via for h in hints] == ["button", "voice", "button"]
    assert all(h.delivered_at is not None and h.source == "curated" for h in hints)
    assert cs.hints_used == 3
    # hint_usage evidence: neutral "assistance" records for the question's competencies in the round
    assert len(evidence) == 9
    assert {e.source for e in evidence} == {"hint_usage"}
    assert {e.signal for e in evidence} == {"neutral"} and {e.dimension for e in evidence} == {"assistance"}
    assert {e.competency_key for e in evidence} == {"algorithms", "data_structures", "problem_solving"}
    assert all(e.transcript_ref["hint_level"] in (1, 2, 3) for e in evidence)
    payload = await service.state_payload(cs_id)
    assert payload.hint_texts == issued and payload.hints_used == 3


async def _org_question_without_hints(env, **overrides) -> uuid.UUID:
    async with session_scope() as db:
        base = await db.get(CodingQuestion, env.questions["two_sum"])
        row = {
            c.name: getattr(base, c.name)
            for c in CodingQuestion.__table__.columns
            if c.name not in ("id", "created_at", "updated_at", "embedding")
        }
        row.update(
            {
                "organization_id": env.organization_id,
                "slug": f"two_sum_nohints_{uuid.uuid4().hex[:6]}",
                "hints": [],
                **overrides,
            }
        )
        question = CodingQuestion(id=uuid.uuid4(), **row)
        db.add(question)
        return question.id


async def test_generated_hints_when_curated_are_missing(service, coding_env, mock_ai) -> None:
    qid = await _org_question_without_hints(coding_env)
    mock_ai.llm.script(
        ModelTask.HINT_GENERATION,
        HintResponse(
            level=1,
            hint_text="Think about what value would complete the pair.",
            speech="Think about the value that completes the pair.",
        ),
        AITimeout("slow"),
        AITimeout("slow"),
    )
    state = await service.start(
        str(coding_env.session_id), str(qid), question_id=None, language=CodeLanguage.PYTHON
    )
    await service.record_update(
        state.coding_session_id, code=PASS_VISIBLE, language=CodeLanguage.PYTHON, version=1
    )
    first = await service.request_hint_detailed(state.coding_session_id)
    assert first.source == "generated" and first.level == 1
    assert first.text == "Think about what value would complete the pair."
    assert first.speech == "Think about the value that completes the pair."
    call = mock_ai.llm.calls_for(ModelTask.HINT_GENERATION)[0]
    prompt = call.messages[1]["content"]
    assert "Requested level: 1" in prompt and "<candidate_code>" in prompt and "PASS_VISIBLE" in prompt
    assert "Fairness and evidence rules" in call.messages[0]["content"]
    second = await service.request_hint_detailed(
        state.coding_session_id
    )  # model down -> deterministic fallback
    assert second.level == 2 and second.source == "generated" and second.text


async def test_generated_level_three_hint_never_contains_code_unless_allowed(
    service, coding_env, mock_ai
) -> None:
    qid = await _org_question_without_hints(coding_env)
    code_hint = HintResponse(
        level=3, hint_text="Use this:\n```python\nseen = {}\n```", speech="Use a dictionary."
    )
    mock_ai.llm.set_default(ModelTask.HINT_GENERATION, code_hint)
    state = await service.start(
        str(coding_env.session_id), str(qid), question_id=None, language=CodeLanguage.PYTHON
    )
    results = [await service.request_hint_detailed(state.coding_session_id) for _ in range(3)]
    assert all("```" not in r.text for r in results)
    system = mock_ai.llm.calls_for(ModelTask.HINT_GENERATION)[-1].messages[0]["content"]
    assert "Never write code" in system


async def test_hints_disabled_by_the_round(service, coding_env) -> None:
    _, _, _, _, session_id = await create_session(
        coding_env.organization_id, round_config(hints_enabled=False)
    )
    state = await service.start(
        str(session_id), str(coding_env.questions["two_sum"]), question_id=None, language=CodeLanguage.PYTHON
    )
    assert state.hints_available == 0
    assert await service.request_hint(state.coding_session_id, requested_via="button") is None


async def test_max_hints_from_round_is_respected(service, coding_env) -> None:
    _, _, _, _, session_id = await create_session(coding_env.organization_id, round_config(max_hints=1))
    state = await service.start(
        str(session_id), str(coding_env.questions["two_sum"]), question_id=None, language=CodeLanguage.PYTHON
    )
    assert state.hints_available == 1
    assert (await service.request_hint(state.coding_session_id, requested_via="button"))[0] == 1
    assert await service.request_hint(state.coding_session_id, requested_via="button") is None


async def test_finish_closes_the_session_and_enqueues_evaluation_once(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    cs_id = state.coding_session_id
    await service.record_update(cs_id, code=HASHMAP, language=CodeLanguage.PYTHON, version=1)
    await service.finish(cs_id, reason="submitted")
    await service.finish(cs_id, reason="submitted")  # idempotent
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
        jobs = (await db.scalars(select(BackgroundJob).where(BackgroundJob.type == "coding.evaluate"))).all()
    assert cs.status == CodingSessionStatus.SUBMITTED.value and cs.ended_at is not None
    assert cs.final_code == HASHMAP
    assert cs.observer_state["end_reason"] == "submitted"
    assert len(jobs) == 1 and jobs[0].payload == {"coding_session_id": cs_id}
    rejected = await service.run(cs_id, kind=RunKind.TEST, code=HASHMAP, language=CodeLanguage.PYTHON)
    assert rejected.status == RunStatus.REJECTED
    assert (
        await service.record_update(cs_id, code=HASHMAP + "\n# more", language=CodeLanguage.PYTHON, version=5)
        is False
    )
    assert await service.request_hint(cs_id, requested_via="button") is None


@pytest.mark.parametrize(
    ("reason", "status"),
    [("time_limit", "timed_out"), ("interviewer_moved_on", "abandoned"), ("session_ended", "abandoned")],
)
async def test_finish_reasons_map_to_statuses(service, coding_env, reason, status) -> None:
    state = await start_two_sum(service, coding_env)
    await service.finish(state.coding_session_id, reason=reason)
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(state.coding_session_id))
    assert cs.status == status


async def test_context_summarises_code_runs_hints_and_explanations(fake_sandbox, mock_ai, coding_env) -> None:
    clock = Clock()
    service = CodingServiceImpl(sandbox=fake_sandbox, ai=mock_ai, clock=clock)
    state = await start_two_sum(service, coding_env)
    cs_id = state.coding_session_id
    clock.advance(30)
    await service.record_update(cs_id, code=PASS_VISIBLE, language=CodeLanguage.PYTHON, version=1)
    clock.advance(10)
    await service.run(cs_id, kind=RunKind.TEST, code="x = (\n# COMPILE_ERROR", language=CodeLanguage.PYTHON)
    clock.advance(10)
    await service.request_hint(cs_id, requested_via="button")
    async with session_scope() as db:
        db.add(
            Transcript(
                session_id=coding_env.session_id,
                seq=1,
                speaker="candidate",
                text="I'll start with a brute force nested loop.",
            )
        )
        db.add(
            Transcript(session_id=coding_env.session_id, seq=2, speaker="interviewer", text="Sounds good.")
        )
    clock.advance(15)
    await service.record_update(cs_id, code=HASHMAP, language=CodeLanguage.PYTHON, version=2)
    context = await service.context(cs_id)
    assert context.question_title == "Two Sum"
    assert context.current_code == HASHMAP and context.language == CodeLanguage.PYTHON
    assert context.previous_code is not None and context.recent_diff
    assert context.snapshots_count >= 3
    assert context.runs[-1]["status"] == "compile_error"
    assert context.last_compile_error and "SyntaxError" in context.last_compile_error
    assert context.hints_used == 1 and len(context.hints) == 1
    assert context.elapsed_s == pytest.approx(65, abs=1)
    assert context.time_limit_s == 1200
    assert context.seconds_since_last_edit == pytest.approx(0, abs=1)
    assert context.expected_concepts and context.optimal_complexity["time"] == "O(n)"
    assert context.candidate_explanations == ["I'll start with a brute force nested loop."]
    assert SECRET_MARK not in json.dumps(context.runs)


async def test_state_payload_survives_a_new_process(fake_sandbox, mock_ai, service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    await service.record_update(
        state.coding_session_id, code=HASHMAP, language=CodeLanguage.PYTHON, version=1
    )
    fresh = CodingServiceImpl(sandbox=fake_sandbox, ai=mock_ai)  # empty cache, e.g. another API instance
    payload = await fresh.state_payload(state.coding_session_id)
    assert payload.code == HASHMAP and payload.language == CodeLanguage.PYTHON
    assert payload.elapsed_s >= 0 and payload.time_limit_s == 1200
    assert "t4" not in json.dumps([t["id"] for t in payload.question.visible_tests])


async def test_observe_runs_the_observer_and_persists_its_state(fake_sandbox, mock_ai, coding_env) -> None:
    clock = Clock()
    service = CodingServiceImpl(sandbox=fake_sandbox, ai=mock_ai, clock=clock)
    state = await start_two_sum(service, coding_env)
    cs_id = state.coding_session_id
    clock.advance(60)
    await service.record_update(cs_id, code=PASS_VISIBLE, language=CodeLanguage.PYTHON, version=1)
    assert await service.observe(cs_id, "edit") is None  # typing
    clock.advance(10)
    mock_ai.llm.script(
        ModelTask.CODE_OBSERVATION,
        CodeObservation(
            should_speak=True,
            kind="question",
            focus="complexity",
            behavior=AvatarBehavior(),
            speech="You've chosen a nested loop here. What is the current time complexity?",
        ),
    )
    decision = await service.observe(cs_id, "idle")
    assert decision is not None and decision.should_speak and "nested loop" in decision.speech
    async with session_scope() as db:
        cs = await db.get(CodingSession, uuid.UUID(cs_id))
    observer_state = cs.observer_state["observer"]
    assert observer_state["comments_made"] == 1
    assert observer_state["baseline_features"]["nested_loops"] is True
    assert observer_state["min_seconds_between_comments"] == 30
    # editor + runs keys survive the observer write (jsonb_set per key)
    assert cs.observer_state["editor"]["version"] == 1
    clock.advance(5)
    assert await service.observe(cs_id, "idle") is None  # min gap


async def test_unknown_ids_raise_not_found(service, coding_env) -> None:
    from app.core.errors import NotFound

    with pytest.raises(NotFound):
        await service.state_payload(str(uuid.uuid4()))
    with pytest.raises(NotFound):
        await service.start(
            str(uuid.uuid4()),
            str(coding_env.questions["two_sum"]),
            question_id=None,
            language=CodeLanguage.PYTHON,
        )
    assert await count(CodingSession) == 0


async def test_change_language_swaps_untouched_starters_and_keeps_edited_code(service, coding_env) -> None:
    state = await start_two_sum(service, coding_env)
    cs_id = state.coding_session_id
    switched = await service.change_language(cs_id, CodeLanguage.GO)
    assert switched.language == CodeLanguage.GO and switched.code == state.question.starter_code["go"]
    edited = switched.code.replace(
        "return []int{}",
        "seen := map[int]int{}\n\tfor i, v := range nums {\n\t\tseen[v] = i\n\t}\n\treturn nil",
    )
    assert await service.record_update(cs_id, code=edited, language=CodeLanguage.GO, version=1) is True
    kept = await service.change_language(cs_id, CodeLanguage.JAVASCRIPT)
    assert kept.language == CodeLanguage.JAVASCRIPT and kept.code == edited  # edited code is never discarded
    refused = await service.change_language(cs_id, CodeLanguage.JAVA)  # not allowed by the round
    assert refused.language == CodeLanguage.JAVASCRIPT
    async with session_scope() as db:
        triggers = (
            await db.scalars(
                select(CodeSnapshot.trigger)
                .where(CodeSnapshot.coding_session_id == uuid.UUID(cs_id))
                .order_by(CodeSnapshot.seq)
            )
        ).all()
    assert triggers == ["autosave", "language_change", "autosave", "language_change"]
    assert await service.record_update(
        cs_id, code=edited + "\n", language=CodeLanguage.JAVASCRIPT, version=2
    ) in (True, False)
