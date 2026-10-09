"""Seeding the catalog and demo jobs into a real PostgreSQL database."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    BankQuestion,
    CodingQuestion,
    Competency,
    InterviewTemplate,
    Job,
    JobRequirement,
    Round,
)
from app.domain.enums import CodeLanguage, RoundType
from app.schemas.policies import IntegrityPolicy, RecordingPolicy
from app.schemas.rounds import RoundConfig
from app.seed.catalog import (
    SeedDependencyError,
    catalog_seed_step,
    demo_jobs_seed_step,
    load_demo_jobs,
    seed_catalog,
)
from app.seed.loaders import SeedCatalog, load_catalog
from app.seed.runner import run_sql_problems_postgres_async
from app.services.coding.contracts import SandboxTestCase

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

MakeOrg = Callable[[], Awaitable[tuple[uuid.UUID, uuid.UUID]]]
Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture(scope="module")
def catalog() -> SeedCatalog:
    return load_catalog()


def _expected_counts(catalog: SeedCatalog) -> dict[str, int]:
    return {
        "competencies": len(catalog.competencies),
        "templates": len(catalog.round_templates),
        "questions": len(catalog.questions),
        "coding_questions": len(catalog.coding_questions),
    }


async def _count(session: AsyncSession, model: Any, organization_id: uuid.UUID | None) -> int:
    column = model.organization_id
    clause = column.is_(None) if organization_id is None else column == organization_id
    return int(await session.scalar(select(func.count()).select_from(model).where(clause)) or 0)


async def _counts(session: AsyncSession, organization_id: uuid.UUID | None) -> dict[str, int]:
    return {
        "competencies": await _count(session, Competency, organization_id),
        "templates": await _count(session, InterviewTemplate, organization_id),
        "questions": await _count(session, BankQuestion, organization_id),
        "coding_questions": await _count(session, CodingQuestion, organization_id),
    }


async def test_schema_created(seed_database: tuple[str, str]) -> None:
    # Report which path built the schema (alembic is expected once migrations exist).
    assert seed_database[1] in {"alembic", "create_all"}


async def test_global_seed_creates_everything_then_is_idempotent(
    seed_sessions: Sessions, catalog: SeedCatalog
) -> None:
    expected = _expected_counts(catalog)
    async with seed_sessions() as session:
        first = await seed_catalog(session, catalog=catalog)
        await session.commit()
    assert {k: getattr(first, k).created for k in expected} == expected
    assert len(first.question_ids_needing_embedding) == expected["questions"]
    assert len(first.embedding_jobs()) == -(-expected["questions"] // 64)

    async with seed_sessions() as session:
        assert await _counts(session, None) == expected
        ids_before = set((await session.execute(select(BankQuestion.external_key, BankQuestion.id))).all())
        second = await seed_catalog(session, catalog=catalog)
        await session.commit()
    for name in expected:
        counts = getattr(second, name)
        assert (counts.created, counts.updated, counts.unchanged) == (0, 0, expected[name]), name

    async with seed_sessions() as session:
        assert await _counts(session, None) == expected
        ids_after = set((await session.execute(select(BankQuestion.external_key, BankQuestion.id))).all())
    assert ids_before == ids_after  # ids are stable across re-seeds


async def test_seeded_rows_honour_contracts(seed_sessions: Sessions, catalog: SeedCatalog) -> None:
    async with seed_sessions() as session:
        await seed_catalog(session, catalog=catalog)
        await session.commit()
        templates = (
            (
                await session.execute(
                    select(InterviewTemplate).where(InterviewTemplate.organization_id.is_(None))
                )
            )
            .scalars()
            .all()
        )
        assert {t.round_type for t in templates} == {r.value for r in RoundType}
        for template in templates:
            assert template.is_default
            config = RoundConfig.model_validate(template.config)
            assert config.round_type.value == template.round_type
            assert abs(sum(c.weight for c in config.rubric) - 100) < 0.01

        coding = (await session.execute(select(CodingQuestion))).scalars().all()
        for problem in coding:
            assert len(problem.hints) == 3
            for case in problem.tests:
                SandboxTestCase.model_validate({k: v for k, v in case.items() if k != "description"})

        question = await session.scalar(
            select(BankQuestion).where(BankQuestion.external_key == "devops.kubernetes.az_failure")
        )
        assert question is not None
        assert question.source == "curated" and question.status == "active"
        assert question.rubric.keys() >= {"excellent", "good", "weak"}
        assert "kubernetes" == question.competency_key and question.round_types


async def test_changed_record_updates_in_place_and_clears_stale_embedding(
    seed_sessions: Sessions, catalog: SeedCatalog, make_org: MakeOrg
) -> None:
    org_id, _ = await make_org()
    async with seed_sessions() as session:
        await seed_catalog(session, org_id, catalog=catalog)
        await session.commit()

    target_key = catalog.questions[0].external_key
    async with seed_sessions() as session:
        row = await session.scalar(
            select(BankQuestion).where(
                BankQuestion.organization_id == org_id, BankQuestion.external_key == target_key
            )
        )
        assert row is not None
        original_id = row.id
        await session.execute(
            text("UPDATE question_bank SET embedding = CAST(:v AS vector) WHERE organization_id = :org"),
            {"v": "[" + ",".join(["0.01"] * 1024) + "]", "org": org_id},
        )
        await session.commit()

    edited = catalog.questions[0].model_copy(
        update={"text": catalog.questions[0].text + " Please be specific."}
    )
    modified = SeedCatalog(
        root=catalog.root,
        competencies=catalog.competencies,
        round_templates=catalog.round_templates,
        questions=[edited, *catalog.questions[1:]],
        coding_questions=catalog.coding_questions,
        jobs=catalog.jobs,
    )
    async with seed_sessions() as session:
        result = await seed_catalog(session, org_id, catalog=modified)
        await session.commit()
    assert result.questions.updated == 1
    assert result.questions.unchanged == len(catalog.questions) - 1
    assert result.question_ids_needing_embedding == [original_id]

    async with seed_sessions() as session:
        row = await session.scalar(select(BankQuestion).where(BankQuestion.id == original_id))
        assert row is not None and row.text.endswith("Please be specific.") and row.embedding is None
        untouched = await session.scalar(
            select(func.count())
            .select_from(BankQuestion)
            .where(BankQuestion.organization_id == org_id, BankQuestion.embedding.is_not(None))
        )
        assert untouched == len(catalog.questions) - 1


async def test_retire_missing_marks_and_restores(
    seed_sessions: Sessions, catalog: SeedCatalog, make_org: MakeOrg
) -> None:
    org_id, _ = await make_org()
    dropped_question = catalog.questions[-1].external_key
    dropped_coding = catalog.coding_questions[-1].slug
    partial = SeedCatalog(
        root=catalog.root,
        competencies=catalog.competencies,
        round_templates=catalog.round_templates,
        questions=catalog.questions[:-1],
        coding_questions=catalog.coding_questions[:-1],
        jobs=catalog.jobs,
    )
    async with seed_sessions() as session:
        await seed_catalog(session, org_id, catalog=catalog)
        await session.commit()
    async with seed_sessions() as session:
        result = await seed_catalog(session, org_id, catalog=partial, retire_missing=True)
        await session.commit()
        assert (result.questions.retired, result.coding_questions.retired) == (1, 1)
        status = await session.scalar(
            select(BankQuestion.status).where(
                BankQuestion.organization_id == org_id, BankQuestion.external_key == dropped_question
            )
        )
        assert status == "retired"
    async with seed_sessions() as session:
        restored = await seed_catalog(session, org_id, catalog=catalog)
        await session.commit()
        assert restored.questions.updated == 1 and restored.coding_questions.updated == 1
        coding_status = await session.scalar(
            select(CodingQuestion.status).where(
                CodingQuestion.organization_id == org_id, CodingQuestion.slug == dropped_coding
            )
        )
        assert coding_status == "active"


async def test_concurrent_seeders_do_not_duplicate_rows(
    seed_sessions: Sessions, catalog: SeedCatalog, make_org: MakeOrg
) -> None:
    org_id, _ = await make_org()

    async def run_once() -> None:
        async with seed_sessions() as session:
            await seed_catalog(session, org_id, catalog=catalog)
            await session.commit()

    await asyncio.gather(run_once(), run_once(), run_once())
    async with seed_sessions() as session:
        assert await _counts(session, org_id) == _expected_counts(catalog)


async def test_load_demo_jobs_creates_jobs_rounds_and_requirements(
    seed_sessions: Sessions, catalog: SeedCatalog, make_org: MakeOrg
) -> None:
    org_id, user_id = await make_org()
    devops_seed = next(j for j in catalog.jobs if j.key == "senior_devops_engineer")
    broken_round = devops_seed.rounds[0].model_copy(
        update={"pinned_coding_slugs": ["slug_that_does_not_exist"]}
    )
    broken_job = devops_seed.model_copy(update={"rounds": [broken_round, *devops_seed.rounds[1:]]})
    broken = SeedCatalog(
        root=catalog.root,
        competencies=catalog.competencies,
        round_templates=catalog.round_templates,
        questions=catalog.questions,
        coding_questions=catalog.coding_questions,
        jobs=[broken_job],
    )
    async with seed_sessions() as session:
        with pytest.raises(SeedDependencyError):
            await load_demo_jobs(session, org_id, user_id, catalog=broken)
        await session.rollback()

    async with seed_sessions() as session:
        await seed_catalog(session, org_id, catalog=catalog)
        result = await load_demo_jobs(session, org_id, user_id, catalog=catalog)
        await session.commit()
    assert set(result.created) == {j.key for j in catalog.jobs}
    assert not result.missing_templates

    async with seed_sessions() as session:
        jobs = (await session.execute(select(Job).where(Job.organization_id == org_id))).scalars().all()
        assert {j.title for j in jobs} == {j.title for j in catalog.jobs}
        by_title = {j.title: j for j in jobs}
        devops = by_title["Senior DevOps Engineer"]
        assert devops.candidate_report_mode == "never"  # owner: candidates never see reports
        assert devops.created_by_id == user_id
        IntegrityPolicy.model_validate(devops.integrity_settings)
        RecordingPolicy.model_validate(devops.recording_settings)
        requirement_keys = set(
            (
                await session.execute(
                    select(JobRequirement.competency_key).where(
                        JobRequirement.job_id == devops.id, JobRequirement.kind == "required_skill"
                    )
                )
            ).scalars()
        )
        assert requirement_keys == {
            "kubernetes",
            "aws",
            "terraform",
            "ci_cd",
            "observability",
            "incident_response",
        }
        rounds = (
            (await session.execute(select(Round).where(Round.job_id == devops.id).order_by(Round.sequence)))
            .scalars()
            .all()
        )
        assert [r.name for r in rounds] == ["Combined AI Round", "Technical Screening", "Behavioral"]
        combined = RoundConfig.model_validate(rounds[0].config)
        assert combined.round_type == RoundType.COMBINED and combined.duration_minutes == 30
        assert combined.coding.enabled and len(combined.coding.question_ids) == 1
        pinned = await session.scalar(
            select(CodingQuestion.slug).where(CodingQuestion.id == uuid.UUID(combined.coding.question_ids[0]))
        )
        assert pinned == "most_frequent_error_service"
        assert all(r.template_id is not None for r in rounds)
        selectable = [j for j in jobs if j.allow_candidate_round_selection]
        assert [j.title for j in selectable] == ["Backend Engineer (Python)"]

    async with seed_sessions() as session:
        again = await load_demo_jobs(session, org_id, user_id, catalog=catalog)
        await session.commit()
        assert not again.created and set(again.existing) == {j.key for j in catalog.jobs}
        total_rounds = await session.scalar(
            select(func.count())
            .select_from(Round)
            .join(Job, Job.id == Round.job_id)
            .where(Job.organization_id == org_id)
        )
        assert total_rounds == sum(len(j.rounds) for j in catalog.jobs)


async def test_seed_runner_steps(seed_sessions: Sessions, make_org: MakeOrg) -> None:
    org_id, user_id = await make_org()
    async with seed_sessions() as session:
        catalog_ctx = await catalog_seed_step(session, {})
        await session.commit()
    assert {
        "catalog_created",
        "catalog_updated",
        "catalog_unchanged",
        "question_ids_needing_embedding",
    } <= set(catalog_ctx)
    async with seed_sessions() as session:
        demo_ctx = await demo_jobs_seed_step(session, {"organization_id": org_id, "recruiter_id": user_id})
        await session.commit()
    assert demo_ctx["demo_jobs_created"] == 5
    assert set(demo_ctx["demo_job_ids"]) == {
        "senior_devops_engineer",
        "backend_engineer_python",
        "frontend_engineer_react",
        "ml_engineer_llm",
        "ai_ml_training_intake",
    }


async def test_sql_reference_solutions_run_on_postgres(
    seed_database: tuple[str, str], seed_engine: Any, catalog: SeedCatalog
) -> None:
    """The validator's PostgreSQL runner: references pass, starters execute,
    and the throwaway schema and role are gone afterwards."""
    sql_problems = [c for c in catalog.coding_questions if c.is_sql]
    assert len(sql_problems) >= 3
    report = await run_sql_problems_postgres_async(sql_problems, database_url=seed_database[0])
    assert report.notes == [] and report.errors == {}
    assert report.jobs == 2 * len(sql_problems)
    async with seed_engine.connect() as conn:
        schemas = (
            await conn.execute(text("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'seedcheck_%'"))
        ).all()
        roles = (
            await conn.execute(text("SELECT rolname FROM pg_roles WHERE rolname LIKE 'seedcheck_%'"))
        ).all()
    assert schemas == [] and roles == []


async def test_postgres_runner_reports_wrong_results_and_refuses_unsafe_sql(
    seed_database: tuple[str, str], catalog: SeedCatalog
) -> None:
    problem = next(c for c in catalog.coding_questions if c.slug == "second_highest_salary")
    wrong = problem.model_copy(
        update={
            "reference_solutions": {
                CodeLanguage.SQL: "SELECT MAX(salary) AS second_highest_salary FROM employee"
            }
        }
    )
    unsafe = problem.model_copy(
        update={"slug": "unsafe", "sql_setup": problem.sql_setup + "\nCOPY employee TO PROGRAM 'id';"}
    )
    report = await run_sql_problems_postgres_async([wrong, unsafe], database_url=seed_database[0])
    assert report.errors["second_highest_salary"][0].startswith(
        "[postgres reference] t1: expected rows [[200]], got [[300]]"
    )
    assert report.errors["unsafe"][0].startswith("[postgres reference] refusing to run on PostgreSQL")


async def test_reseeding_an_embedded_catalog_keeps_vectors(
    seed_sessions: Sessions, catalog: SeedCatalog, make_org: MakeOrg
) -> None:
    org_id, _ = await make_org()
    async with seed_sessions() as session:
        await seed_catalog(session, org_id, catalog=catalog)
        await session.commit()
    async with seed_sessions() as session:
        vector = "[" + ",".join(["0.02"] * 1024) + "]"
        for table in ("question_bank", "coding_questions", "competencies"):
            await session.execute(
                text(f"UPDATE {table} SET embedding = CAST(:v AS vector) WHERE organization_id = :org"),  # noqa: S608
                {"v": vector, "org": org_id},
            )
        await session.commit()
    async with seed_sessions() as session:
        again = await seed_catalog(session, org_id, catalog=catalog)
        await session.commit()
    assert again.question_ids_needing_embedding == []
    assert again.coding_question_ids_needing_embedding == []
    assert again.competency_ids_needing_embedding == []
    edited = catalog.coding_questions[0].model_copy(
        update={"title": catalog.coding_questions[0].title + " II"}
    )
    modified = SeedCatalog(
        root=catalog.root,
        competencies=catalog.competencies,
        round_templates=catalog.round_templates,
        questions=catalog.questions,
        coding_questions=[edited, *catalog.coding_questions[1:]],
        jobs=catalog.jobs,
    )
    async with seed_sessions() as session:
        result = await seed_catalog(session, org_id, catalog=modified)
        await session.commit()
        missing = await session.scalar(
            select(func.count())
            .select_from(CodingQuestion)
            .where(CodingQuestion.organization_id == org_id, CodingQuestion.embedding.is_(None))
        )
    assert result.coding_questions.updated == 1 and len(result.coding_question_ids_needing_embedding) == 1
    assert missing == 1
