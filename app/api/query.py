from fastapi import APIRouter, Depends

from app.middleware.auth import User, get_current_user
from app.models import ChatResponse, QueryRequest
from app.services.rag_service import run_rag


router = APIRouter(tags=["query"])


@router.post("/query", response_model=ChatResponse)
async def query(
    body: QueryRequest,
    user: User = Depends(get_current_user),
    ) -> ChatResponse:
    return run_rag(
        body.question,
        flags={
            "top_k": body.top_k,
            "search_mode": body.search_mode,
            "enable_rerank": body.enable_rerank,
            "enable_hyde": body.enable_hyde,
            "enable_crag": body.enable_crag,
        },
    )
