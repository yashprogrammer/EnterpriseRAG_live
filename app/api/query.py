import uuid
from app.config import settings
from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from app.middleware.rate_limiter import is_allowed_user
from app.security.content_moderation import moderate_and_redact
from app.security.input_guard import check_input_safe
from app.security.input_restructuring import count_tokens, restructure_input
from app.security.token_budget import check_budget, consume_budget

from app.core.graph import graph
from app.middleware.auth import User, get_current_user
from app.models import ChatResponse, QueryRequest, PendingSQLBlock



router = APIRouter(tags=["query"])

def _estimate_tokens(question: str) -> int:
    return count_tokens(question) + settings.reserved_output_tokens


class SqlExecuteRequest(BaseModel):
    query_id: str
    approved: bool




@router.post("/query", response_model=ChatResponse)
async def query(
    body: QueryRequest,
    user: User = Depends(get_current_user),
) -> ChatResponse:
    # Layer 3: per-user sliding-window rate limit
    allowed, _, _ = is_allowed_user(
        user.username,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Layer 3 (token budget): per-user per-day
    estimated = _estimate_tokens(body.question)
    ok, remaining = check_budget(user.username, estimated)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=(
                f"You have {remaining} tokens remaining today; "
                f"this request estimated to use {estimated}."
            ),
        )

    # Layer 1 (input restructuring): truncate/summarize if too long
    restructured, _method = restructure_input(body.question)

    # Layer 2 (LLM-Guard input scan): injection / ban-topic / toxicity
    guard_allowed, guard_reason = check_input_safe(restructured)
    if not guard_allowed:
        raise HTTPException(status_code=400, detail=f"injection_blocked: {guard_reason}")

    # Layer 4 (content moderation in): PII redaction + toxicity
    mod_allowed, moderated_in, mod_reason = moderate_and_redact(restructured)
    if not mod_allowed:
        raise HTTPException(status_code=400, detail=f"content_blocked: {mod_reason}")

    flags = {
        "top_k": body.top_k,
        "search_mode": body.search_mode,
        "enable_rerank": body.enable_rerank,
        "enable_hyde": body.enable_hyde,
        "enable_crag": body.enable_crag,
        "enable_self_reflective": body.enable_self_reflective,
    }
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    result = graph.invoke(
        {
            "question": moderated_in,
            "user_id": user.username,
            "flags": flags,
        },
        config=config,
    )

    if "__interrupt__" in result:
        intr = result["__interrupt__"][0].value
        return ChatResponse(
            answer="",
            sources=[],
            confidence=0.0,
            pending_sql=PendingSQLBlock(
                sql=intr.get("sql", ""),
                query_id=thread_id,
                explanation=intr.get("explanation", ""),
            ),
        )

    response = ChatResponse(
        answer=result.get("final_answer", ""),
        sources=result.get("sources", []),
        confidence=result.get("confidence", 0.0),
        cache_hit=result.get("cache_hit", False),
        metadata=result.get("metadata", {}),
    )

    # Layer 6 (content moderation out): redact PII before returning
    out_allowed, redacted, _ = moderate_and_redact(response.answer)
    if not out_allowed:
        raise HTTPException(status_code=500, detail="output_blocked")
    response.answer = redacted

    # Consume the budget (only when the call actually succeeded)
    consume_budget(user.username, estimated)

    return response


@router.post("/query/sql/execute", response_model=ChatResponse)
async def execute_sql(
    body: SqlExecuteRequest,
    user: User = Depends(get_current_user),
) -> ChatResponse:
    
    config = {"configurable": {"thread_id": body.query_id}}

    result = graph.invoke(
        Command(resume={"approved": body.approved}),
        config=config,
    )

    return ChatResponse(
        answer=result.get("final_answer", "SQL query was not approved."),
        sources=result.get("sources", []),
        confidence=result.get("confidence", 0.0),
        cache_hit=result.get("cache_hit", False),
        metadata=result.get("metadata", {}),
    )
