from __future__ import annotations

from loguru import logger

from app.config import settings
from app.models import (
    ChatResponse,
    ResponseMetadata,
    RetrievedChunk,
    RetrievedChunkPreview,
)
from app.security.spotlighting import build_spotlighted_context
from app.security.system_prompt import build_system_prompt
from app.services.embedding_service import embed_texts
from app.services.reranking import Reranker
from app.services.llm_service import generate
from app.services.vector_store import search, hybrid_search, sparse_search


def _enable_rerank(flags: dict | None) -> bool:
    if not isinstance(flags, dict):
        return False
    return bool(flags.get("enable_rerank", False))


def _top_k_from_flags(flags: dict | int | None) -> int:
    if flags is None:
        return 5
    if isinstance(flags, int):
        return flags
    return int(flags.get("top_k", 5))

def _search_mode(flags: dict | None) -> str:
    if not isinstance(flags, dict):
        return "dense"
    return flags.get("search_mode", "dense")




def _retrieve(question: str, flags: dict | None = None) -> list[RetrievedChunk]:
    final_top_k = _top_k_from_flags(flags)
    mode = _search_mode(flags)
    rerank = _enable_rerank(flags)

    retrieve_k = settings.reranker_initial_top_k if rerank else final_top_k


    if mode == "sparse":
        chunks = sparse_search(question, top_k=retrieve_k)
    elif mode == "hybrid":
        query_embedding = embed_texts([question])[0]
        chunks =  hybrid_search(query_embedding, question, top_k=retrieve_k)
    else:
        query_embedding = embed_texts([question])[0]
        chunks =  search(query_embedding, top_k=retrieve_k)

    if rerank and chunks:
        chunks = Reranker().rerank(question, chunks, top_k=final_top_k)
    else:
        chunks = chunks[:final_top_k]
    return chunks

def _generate(question: str, chunks: list[RetrievedChunk]) -> ChatResponse:
    spotlighted = build_spotlighted_context(chunks)
    system = build_system_prompt()
    user_msg = f"{spotlighted}\n\nQuestion: {question}"
    raw = generate(system, user_msg)["text"]
    chunk_previews = [
        RetrievedChunkPreview(text=c.text, source=c.source, score=c.score) for c in chunks
    ]
    return ChatResponse(
        answer=raw,
        sources=list({c.source for c in chunks}),
        confidence=0.7,  # placeholder; real confidence comes with CRAG (L5)
        metadata=ResponseMetadata(
            route="rag",
            retrieved_chunks=chunk_previews,
        ),
    )



def run_rag(question: str, flags: dict | int | None = None) -> ChatResponse:
    mode = _search_mode(flags) if isinstance(flags, dict) else "dense"
    rerank = _enable_rerank(flags) if isinstance(flags, dict) else False
    logger.info("L3 RAG | mode={} rerank={} top_k={}", mode, rerank, _top_k_from_flags(flags))
    chunks = _retrieve(question, flags=flags if isinstance(flags, dict) else None)
    return _generate(question, chunks)


def run_rag_with_trace(
    question: str, flags: dict | int | None = None
) -> tuple[ChatResponse, list[RetrievedChunk]]:
    chunks = _retrieve(question, flags=flags if isinstance(flags, dict) else None)
    response = _generate(question, chunks)
    return response, chunks



run_rag_with_trace_no_cache = run_rag_with_trace