"""Coding question management (CONTRACTS §4, catalog).

* ``GET /coding-questions`` / ``GET /coding-questions/{id}`` (``jobs:read``):
  the organization's problems plus the global curated catalog (read-only).
  The list returns light summary rows by default (``view=summary``: no prompt,
  tests, starters or solutions; test counts instead); ``view=full`` returns
  every field except reference solutions.
* ``POST /coding-questions`` / ``PATCH /coding-questions/{id}``
  (``question_bank:write``): organization-scoped; global rows can never be
  modified through the API (seeded from ``samples/coding``). Starter code for
  function languages without one is generated from the signature.
* ``POST /coding-questions/{id}/validate`` (``question_bank:write``): runs every
  reference solution against all tests in the sandbox and reports per-language
  results (optionally also compiles the starters).

Hidden tests and reference solutions are staff data: they are returned here
(staff only) and never in any candidate payload.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import ValidationError
from sqlalchemy import func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONPATH
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import audit
from app.core.deps import DbSession, PaginationParams, require_permission, resolve_org_id
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.ratelimit import enforce_rate_limit
from app.core.rbac import Permission, Principal
from app.db.models import CodingQuestion
from app.domain.enums import BankStatus, CodeLanguage, Difficulty, QuestionSource
from app.schemas.api.coding import (
    CodingQuestionCreate,
    CodingQuestionOut,
    CodingQuestionSummaryOut,
    CodingQuestionUpdate,
    CodingQuestionValidation,
    slug_from_title,
)
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.services.coding.sandbox_client import get_sandbox_client
from app.services.coding.starters import starter_for
from app.services.coding.validation import validate_question
from app.services.question_bank.embeddings import (
    coding_question_text,
    enqueue_embedding,
    text_revision,
    with_embedding_flag,
)

router = APIRouter(prefix="/coding-questions", tags=["coding-questions"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.QUESTION_BANK_WRITE))]

_EMBED_FIELDS = ("title", "prompt_markdown", "expected_concepts")  # = coding_question_text() inputs
_SUMMARY_COLUMNS = (
    CodingQuestion.id,
    CodingQuestion.organization_id,
    CodingQuestion.slug,
    CodingQuestion.title,
    CodingQuestion.difficulty,
    CodingQuestion.competency_keys,
    CodingQuestion.role_tags,
    CodingQuestion.languages,
    CodingQuestion.time_limit_minutes,
    CodingQuestion.source,
    CodingQuestion.status,
    CodingQuestion.created_at,
    CodingQuestion.updated_at,
    CodingQuestion.embedding.is_not(None).label("has_embedding"),
    func.jsonb_array_length(CodingQuestion.tests).label("test_count"),
    func.jsonb_array_length(
        func.jsonb_path_query_array(CodingQuestion.tests, literal("$[*] ? (@.hidden == true)", JSONPATH))
    ).label("hidden_test_count"),
)
VALIDATE_RATE_LIMIT = 20
VALIDATE_WINDOW_S = 60.0


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _get_visible(db: DbSession, principal: Principal, question_id: uuid.UUID) -> CodingQuestion:
    question = await db.get(CodingQuestion, question_id)
    if question is None:
        raise NotFound("Coding question not found.", code="coding_question_not_found")
    if (
        question.organization_id is not None
        and not principal.is_super_admin
        and question.organization_id != principal.organization_id
    ):
        raise NotFound("Coding question not found.", code="coding_question_not_found")
    return question


async def _enqueue_embedding(db: DbSession, question: CodingQuestion) -> str | None:
    """Queue ``question_bank.embed`` for a question whose vector is missing/stale.

    The job is dispatched after the request commits; the revision (hash of the
    new embedding text) keeps a job still running on the previous text from
    absorbing this request.
    """
    return await enqueue_embedding(
        db, coding_question_ids=[question.id], revision=text_revision(coding_question_text(question))
    )


def _fill_starters(row: dict[str, Any]) -> list[str]:
    """Generate starter code for function languages that have none."""
    signature = row.get("signature") or {}
    if not signature:
        return []
    starters = dict(row.get("starter_code") or {})
    generated: list[str] = []
    for lang in row.get("languages") or []:
        if lang != CodeLanguage.SQL.value and not (starters.get(lang) or "").strip():
            starters[lang] = starter_for(signature, lang, description=row.get("title"))
            generated.append(lang)
    row["starter_code"] = starters
    return generated


@router.get(
    "",
    response_model=Page[CodingQuestionSummaryOut] | Page[CodingQuestionOut],
    summary="List coding questions (organization + global catalog)",
)
async def list_coding_questions(
    principal: Reader,
    db: DbSession,
    page: PaginationParams,
    organization_id: Annotated[
        uuid.UUID | None, Query(description="SUPER_ADMIN: organization to list")
    ] = None,
    scope: Annotated[
        Literal["all", "organization", "global"], Query(description="Which rows to include")
    ] = "all",
    difficulty: Difficulty | None = None,
    language: CodeLanguage | None = None,
    competency: Annotated[str | None, Query(max_length=80)] = None,
    source: QuestionSource | None = None,
    status_filter: Annotated[BankStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100, description="Search title or slug")] = None,
    view: Annotated[
        Literal["summary", "full"],
        Query(
            description="summary (default): list fields and test counts; "
            "full: every field except reference solutions (~7 KB per problem)"
        ),
    ] = "summary",
) -> Page[CodingQuestionSummaryOut] | Page[CodingQuestionOut]:
    org_id = resolve_org_id(principal, organization_id)
    stmt = select(CodingQuestion)
    if scope == "organization":
        stmt = stmt.where(CodingQuestion.organization_id == org_id)
    elif scope == "global":
        stmt = stmt.where(CodingQuestion.organization_id.is_(None))
    else:
        stmt = stmt.where(
            or_(CodingQuestion.organization_id == org_id, CodingQuestion.organization_id.is_(None))
        )
    if difficulty is not None:
        stmt = stmt.where(CodingQuestion.difficulty == difficulty.value)
    if language is not None:
        stmt = stmt.where(CodingQuestion.languages.contains([language.value]))
    if competency:
        stmt = stmt.where(CodingQuestion.competency_keys.contains([competency]))
    if source is not None:
        stmt = stmt.where(CodingQuestion.source == source.value)
    if status_filter is not None:
        stmt = stmt.where(CodingQuestion.status == status_filter.value)
    if q:
        pattern = f"%{_escape_like(q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(CodingQuestion.title).like(pattern, escape="\\"),
                CodingQuestion.slug.like(pattern, escape="\\"),
            )
        )
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    ordered = stmt.order_by(CodingQuestion.organization_id.is_(None), CodingQuestion.title, CodingQuestion.id)
    if view == "summary":
        # Page ids first, then the summary columns for those rows only: the
        # test counts read the (TOASTed) tests column, which must not happen
        # for every row before the sort.
        page_ids = page.apply(ordered.with_only_columns(CodingQuestion.id)).subquery()
        summary_stmt = (
            select(*_SUMMARY_COLUMNS)
            .join(page_ids, page_ids.c.id == CodingQuestion.id)
            .order_by(CodingQuestion.organization_id.is_(None), CodingQuestion.title, CodingQuestion.id)
        )
        summaries = (await db.execute(summary_stmt)).all()
        return Page[CodingQuestionSummaryOut](
            items=[CodingQuestionSummaryOut.from_row(r) for r in summaries],
            total=total,
            limit=page.limit,
            offset=page.offset,
        )
    rows = (await db.execute(page.apply(with_embedding_flag(ordered, CodingQuestion)))).all()
    return Page[CodingQuestionOut](
        items=[
            CodingQuestionOut.from_row(r, include_solutions=False, has_embedding=flag) for r, flag in rows
        ],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=CodingQuestionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a coding question",
)
async def create_coding_question(
    body: CodingQuestionCreate, request: Request, principal: Writer, db: DbSession
) -> CodingQuestionOut:
    org_id = resolve_org_id(principal, body.organization_id)
    row = body.to_row()
    generated = _fill_starters(row)
    question = CodingQuestion(
        id=uuid.uuid4(),
        organization_id=org_id,
        slug=body.slug or slug_from_title(body.title),
        source=QuestionSource.CURATED.value,
        **row,
    )
    db.add(question)
    await db.flush()  # unique (organization_id, slug) -> 409
    await db.refresh(question)
    embedding_job = await _enqueue_embedding(db, question)
    await audit(
        db,
        principal,
        "coding_question.create",
        resource_type="coding_question",
        resource_id=str(question.id),
        request=request,
        organization_id=org_id,
        details={
            "slug": question.slug,
            "languages": question.languages,
            "tests": len(question.tests),
            "generated_starters": generated,
            "embedding_job_id": embedding_job,
        },
    )
    return CodingQuestionOut.from_row(question)


@router.get("/{question_id}", response_model=CodingQuestionOut, summary="Get a coding question")
async def get_coding_question(question_id: uuid.UUID, principal: Reader, db: DbSession) -> CodingQuestionOut:
    return CodingQuestionOut.from_row(await _get_visible(db, principal, question_id))


@router.patch(
    "/{question_id}", response_model=CodingQuestionOut, summary="Update an organization's coding question"
)
async def update_coding_question(
    question_id: uuid.UUID, body: CodingQuestionUpdate, request: Request, principal: Writer, db: DbSession
) -> CodingQuestionOut:
    question = await _get_visible(db, principal, question_id)
    if question.organization_id is None:
        raise Forbidden(
            "Global catalog questions are read-only. Create an organization copy instead.",
            code="global_read_only",
        )
    current = CodingQuestionOut.from_row(question).model_dump(mode="json")
    changes = body.model_dump(mode="json", exclude_unset=True)
    if not changes:
        return CodingQuestionOut.from_row(question)
    merged = {
        key: current[key]
        for key in CodingQuestionCreate.model_fields
        if key in current and key not in ("organization_id",)
    }
    merged.update(changes)
    if not merged.get("signature"):
        merged["signature"] = None
    try:
        validated = CodingQuestionCreate.model_validate(merged)
    except ValidationError as exc:
        errors = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", "")} for e in exc.errors()[:20]
        ]
        raise ValidationFailed("The coding question is invalid.", details={"errors": errors}) from exc
    row = validated.to_row()
    generated = _fill_starters(row)
    changed = sorted(k for k, v in row.items() if getattr(question, k) != v)
    for key in changed:
        setattr(question, key, row[key])
    if validated.slug and validated.slug != question.slug:
        question.slug = validated.slug
        changed.append("slug")
    stale_embedding = any(k in _EMBED_FIELDS for k in changed)
    if stale_embedding:
        question.embedding = None
        # Always write the NULL: an embedding job may have stored a vector of
        # the previous text after this row was loaded.
        flag_modified(question, "embedding")
    if changed:
        await db.flush()
        await db.refresh(question)
        embedding_job = await _enqueue_embedding(db, question) if stale_embedding else None
        await audit(
            db,
            principal,
            "coding_question.update",
            resource_type="coding_question",
            resource_id=str(question.id),
            request=request,
            organization_id=question.organization_id,
            details={"changed": changed, "generated_starters": generated, "embedding_job_id": embedding_job},
        )
    return CodingQuestionOut.from_row(question)


@router.post(
    "/{question_id}/validate",
    response_model=CodingQuestionValidation,
    summary="Run reference solutions in the sandbox",
)
async def validate_coding_question(
    question_id: uuid.UUID,
    request: Request,
    principal: Writer,
    db: DbSession,
    include_starters: Annotated[
        bool, Query(description="Also check that every starter compiles and runs")
    ] = False,
) -> CodingQuestionValidation:
    question = await _get_visible(db, principal, question_id)
    await enforce_rate_limit(
        "coding_validate",
        str(principal.user_id),
        limit=VALIDATE_RATE_LIMIT,
        window_s=VALIDATE_WINDOW_S,
        message="Too many validation runs. Please wait a moment.",
    )
    result = await validate_question(question, get_sandbox_client(), include_starters=include_starters)
    await audit(
        db,
        principal,
        "coding_question.validate",
        resource_type="coding_question",
        resource_id=str(question.id),
        request=request,
        organization_id=question.organization_id or principal.organization_id,
        details={
            "ok": result.ok,
            "results": {r.language.value: f"{r.status} {r.passed}/{r.total}" for r in result.results},
        },
    )
    return result
