"""Question bank: CRUD, semantic search and AI generation (drafts for review)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select

from app.core.audit import audit
from app.core.deps import DbSession, OrgScope, PaginationParams, require_permission
from app.core.ratelimit import enforce_rate_limit
from app.core.rbac import Permission, Principal
from app.db.models import BankQuestion
from app.domain.enums import BankStatus, Difficulty, QuestionKind, QuestionSource, RoundType
from app.schemas.api.common import ERROR_RESPONSES, Page
from app.schemas.api.question_bank import (
    BankQuestionCreate,
    BankQuestionOut,
    BankQuestionUpdate,
    QuestionGenerateRequest,
    QuestionGenerateResponse,
    QuestionSearchRequest,
    QuestionSearchResponse,
    SkippedQuestion,
)
from app.services.jobs.catalog import ensure_org_owned
from app.services.question_bank.embeddings import with_embedding_flag
from app.services.question_bank.service import (
    create_question,
    generate_questions,
    get_question,
    question_list_query,
    search_questions,
    update_question,
)

router = APIRouter(tags=["question-bank"], responses=ERROR_RESPONSES)

Reader = Annotated[Principal, Depends(require_permission(Permission.JOBS_READ))]
Writer = Annotated[Principal, Depends(require_permission(Permission.QUESTION_BANK_WRITE))]

GENERATE_LIMIT = 20
GENERATE_WINDOW_SECONDS = 600


@router.get("/question-bank", response_model=Page[BankQuestionOut], summary="List bank questions")
async def list_questions(
    principal: Reader,
    org_id: OrgScope,
    db: DbSession,
    page: PaginationParams,
    competency_key: Annotated[str | None, Query(max_length=80)] = None,
    difficulty: Difficulty | None = None,
    kind: QuestionKind | None = None,
    status_filter: Annotated[BankStatus | None, Query(alias="status")] = None,
    source: QuestionSource | None = None,
    round_type: RoundType | None = None,
    role: Annotated[str | None, Query(max_length=60)] = None,
    scope: Literal["all", "global", "organization"] = "all",
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[BankQuestionOut]:
    stmt = question_list_query(
        org_id,
        competency_key=competency_key,
        difficulty=difficulty,
        kind=kind,
        status=status_filter,
        source=source,
        round_type=round_type,
        role=role,
        scope=scope,
        q=q,
    )
    total = int(await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    ordered = stmt.order_by(
        BankQuestion.competency_key, BankQuestion.difficulty, BankQuestion.created_at, BankQuestion.id
    )
    rows = (await db.execute(page.apply(with_embedding_flag(ordered, BankQuestion)))).all()
    return Page[BankQuestionOut](
        items=[BankQuestionOut.from_row(row, has_embedding=flag) for row, flag in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "/question-bank",
    response_model=BankQuestionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a question",
)
async def create_question_endpoint(
    body: BankQuestionCreate, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> BankQuestionOut:
    row = await create_question(db, organization_id=org_id, created_by_id=principal.user_id, body=body)
    await db.refresh(row)
    await audit(
        db,
        principal,
        "question_bank.create",
        resource_type="bank_question",
        resource_id=row.id,
        request=request,
        organization_id=org_id,
        details={"competency_key": row.competency_key, "difficulty": row.difficulty},
    )
    return BankQuestionOut.from_row(row)


@router.post(
    "/question-bank/search", response_model=QuestionSearchResponse, summary="Semantic question search"
)
async def search(
    body: QuestionSearchRequest, principal: Reader, org_id: OrgScope, db: DbSession
) -> QuestionSearchResponse:
    outcome = await search_questions(
        db,
        org_id,
        query=body.query,
        competency_key=body.competency_key,
        difficulty=body.difficulty,
        kind=body.kind,
        round_type=body.round_type,
        status=body.status,
        limit=body.limit,
    )
    items = [
        BankQuestionOut.from_row(
            hit.question,
            score=round(hit.score, 4) if hit.score is not None else None,
            vector_similarity=round(hit.vector_similarity, 4) if hit.vector_similarity is not None else None,
            rerank_score=round(hit.rerank_score, 4) if hit.rerank_score is not None else None,
        )
        for hit in outcome.hits
    ]
    return QuestionSearchResponse(items=items, total=len(items), mode=outcome.mode, reranked=outcome.reranked)


@router.post(
    "/question-bank/generate",
    response_model=QuestionGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate draft questions with the AI model",
)
async def generate(
    body: QuestionGenerateRequest, request: Request, principal: Writer, org_id: OrgScope, db: DbSession
) -> QuestionGenerateResponse:
    await enforce_rate_limit(
        "question_generate", str(principal.user_id), limit=GENERATE_LIMIT, window_s=GENERATE_WINDOW_SECONDS
    )
    outcome = await generate_questions(db, organization_id=org_id, created_by_id=principal.user_id, body=body)
    await audit(
        db,
        principal,
        "question_bank.generate",
        resource_type="bank_question",
        request=request,
        organization_id=org_id,
        details={
            "competency_key": body.competency_key,
            "difficulty": body.difficulty.value,
            "requested": body.count,
            "created": [str(r.id) for r in outcome.created],
            "skipped": len(outcome.skipped),
            "model_call_id": outcome.model_call_id,
        },
    )
    return QuestionGenerateResponse(
        created=[BankQuestionOut.from_row(r) for r in outcome.created],
        skipped=[SkippedQuestion.model_validate(item) for item in outcome.skipped],
        requested=body.count,
        generated=outcome.generated,
        model_call_id=outcome.model_call_id,
    )


@router.get("/question-bank/{question_id}", response_model=BankQuestionOut, summary="Get a question")
async def get_question_endpoint(
    question_id: uuid.UUID, principal: Reader, org_id: OrgScope, db: DbSession
) -> BankQuestionOut:
    return BankQuestionOut.from_row(await get_question(db, question_id, org_id))


@router.patch(
    "/question-bank/{question_id}", response_model=BankQuestionOut, summary="Update an organization question"
)
async def update_question_endpoint(
    question_id: uuid.UUID,
    body: BankQuestionUpdate,
    request: Request,
    principal: Writer,
    org_id: OrgScope,
    db: DbSession,
) -> BankQuestionOut:
    row = await get_question(db, question_id, org_id)
    ensure_org_owned(row, org_id, what="question")
    changed = await update_question(db, row, body, org_id)
    if changed:
        await audit(
            db,
            principal,
            "question_bank.update",
            resource_type="bank_question",
            resource_id=row.id,
            request=request,
            organization_id=org_id,
            details={"changes": changed, "status": row.status},
        )
    await db.refresh(row)
    return BankQuestionOut.from_row(row)
