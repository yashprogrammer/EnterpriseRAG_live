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
from app.services.crag import crag_pipeline
from app.services.embedding_service import embed_texts
from app.services.reranking import Reranker
from app.services.llm_service import generate
from app.services.vector_store import search, hybrid_search, sparse_search
from app.services.self_reflective import reflect_on_answer, should_regenerate
from app.services.hyde import HyDERetriever
from app.services.router_service import classify_intent
from app.services.sql_service import SQLService
from app.services.query_cache_service import query_cache




def _flag(flags: dict | None, key: str, default):
    if not isinstance(flags, dict):
        return default
    return flags.get(key, default)








def _retrieve(question: str, flags: dict | None = None) -> list[RetrievedChunk]:
    final_top_k = int(_flag(flags, "top_k", 5))
    mode = _flag(flags, "search_mode", "dense")
    rerank = bool(_flag(flags, "enable_rerank", False))
    hyde = bool(_flag(flags, "enable_hyde", False))
    enable_crag = bool(_flag(flags, "enable_crag", settings.crag_enabled_by_default))

    retrieve_k = settings.reranker_initial_top_k if rerank else final_top_k


    if hyde:
        chunks = HyDERetriever().retrieve(question, top_k=retrieve_k)
    elif mode == "sparse":
        chunks = sparse_search(question, top_k=retrieve_k)
    elif mode == "hybrid":
        query_embedding = embed_texts([question])[0]
        chunks = hybrid_search(query_embedding, question, top_k=retrieve_k)
    else:
        query_embedding = embed_texts([question])[0]
        chunks = search(query_embedding, top_k=retrieve_k)

    if rerank and chunks:
        chunks = Reranker().rerank(question, chunks, top_k=final_top_k)
    else:
        chunks = chunks[:final_top_k]

    # CRAG: grade chunks + fall back to web search if irrelevant
    chunks, evaluation, used_web = crag_pipeline(
        question=question,
        chunks=chunks,
        enable_crag=enable_crag,
    )
    logger.info(
        "CRAG | enabled={} score={} label={} used_web={}",
        enable_crag,
        evaluation.relevance_score,
        evaluation.relevance_label,
        used_web,
    )

    return chunks


def _generate(
    question: str,
    chunks: list[RetrievedChunk],
    flags: dict | None = None,
) -> ChatResponse:
    enable_self_reflective = bool(_flag(flags, "enable_self_reflective", False))

    spotlighted = build_spotlighted_context(chunks)
    system = build_system_prompt()

    def _raw(q: str) -> str:
        return generate(system, f"{spotlighted}\n\nQuestion: {q}")["text"]

    working_q = question
    raw = _raw(working_q)

    # Self-RAG: reflect on the answer; refine the question and retry if weak.
    iterations = 0
    last_score: float | None = None
    final_refined: str | None = None
    if enable_self_reflective:
        while True:
            reflection = reflect_on_answer(
                question=working_q,
                answer=raw,
                context=spotlighted,
            )
            last_score = float(reflection.reflection_score)
            if not should_regenerate(reflection, iterations):
                break
            final_refined = reflection.refined_question or working_q
            working_q = final_refined
            raw = _raw(working_q)
            iterations += 1

    chunk_previews = [
        RetrievedChunkPreview(text=c.text, source=c.source, score=c.score) for c in chunks
    ]
    return ChatResponse(
        answer=raw,
        sources=list({c.source for c in chunks}),
        confidence=0.7,
        metadata=ResponseMetadata(
            route="rag",
            retrieved_chunks=chunk_previews,
            reflection_iterations=iterations,
            reflection_score=last_score,
            refined_question=final_refined,
        ),
    )



def _run_sql_inline(question: str) -> ChatResponse:
    import json as _json

    svc = SQLService()
    try:
        gen = svc.generate_sql(question)
        sql = gen["sql"]
        rows = svc.execute_sql(sql)
        if not rows:
            answer = "No results."
            row_chunks: list[RetrievedChunkPreview] = []
        else:
            answer = f"Query results:\n```\n{_json.dumps(rows, indent=2, default=str)}\n```"
            row_chunks = [
                RetrievedChunkPreview(
                    text=_json.dumps(row, default=str),
                    source="query_results",
                    score=1.0,
                )
                for row in rows
            ]
        return ChatResponse(
            answer=answer,
            sources=["query_results"],
            confidence=0.9,
            metadata=ResponseMetadata(route="sql", retrieved_chunks=row_chunks),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("SQL path failed: {}", exc)
        return ChatResponse(
            answer=f"SQL generation/execution failed: {exc}",
            sources=[],
            confidence=0.0,
            metadata=ResponseMetadata(route="sql", retrieved_chunks=[]),
        )

def _run_hybrid_inline(
    question: str, flags: dict | None
) -> tuple[ChatResponse, list[RetrievedChunk]]:
    chunks = _retrieve(question, flags=flags)
    svc = SQLService()
    rows: list[dict] = []
    sql_text = ""

    try:
        gen = svc.generate_sql(question)
        sql_text = gen.get("sql", "")
        rows = svc.execute_sql(sql_text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hybrid SQL leg failed: {}", exc)

    spotlighted = build_spotlighted_context(chunks)
    system = (
        "You are an SRE assistant. Synthesize the database query results "
        "AND the retrieved documents into a single coherent answer. "
        "Cite [database query] for SQL results and [filename] for documents."
    )

    sql_section = ""

    if rows:
        import json as _json
        sql_section = f"\n=== Database Results ===\n```\n{_json.dumps(rows, indent=2, default=str)}\n```\n"
    user_msg = f"{sql_section}{spotlighted}\n\nQuestion: {question}"
    raw = generate(system, user_msg)["text"]

    response = ChatResponse(
        answer=raw,
        sources=["database query"] + list({c.source for c in chunks}),
        confidence=0.8,
        metadata=ResponseMetadata(
            route="hybrid",
            retrieved_chunks=[
                RetrievedChunkPreview(text=c.text, source=c.source, score=c.score)
                for c in chunks
            ],
        ),
    )
    return response, chunks




def run_rag(question: str, flags: dict | int | None = None) -> ChatResponse:
    cache_ctx = (
        _cache_context(flags) if isinstance(flags, dict) else _cache_context(None)
    )
    cached = query_cache.get_rag_answer(question, cache_ctx)
    if cached is not None:
        resp = ChatResponse(**cached)
        resp.cache_hit = True  # set on the schema (added in L8)
        resp.metadata.cache_hit = True
        return resp

    intent = classify_intent(question)
    logger.info(
        "L8 query | intent={} mode={} rerank={} hyde={} crag={} self_rag={} top_k={}",
        intent,
        _flag(flags, "search_mode", "dense"),
        _flag(flags, "enable_rerank", False),
        _flag(flags, "enable_hyde", False),
        _flag(flags, "enable_crag", settings.crag_enabled_by_default),
        _flag(flags, "enable_self_reflective", False),
        int(_flag(flags, "top_k", 5)),
    )
    if intent == "sql":
        response = _run_sql_inline(question)
    elif intent == "hybrid":
        response, _ = _run_hybrid_inline(
            question, flags if isinstance(flags, dict) else None
        )
    else:
        chunks = _retrieve(question, flags=flags if isinstance(flags, dict) else None)
        response = _generate(question, chunks, flags=flags if isinstance(flags, dict) else None)

    query_cache.set_rag_answer(question, response.model_dump(), cache_ctx)
    return response


def _cache_context(flags: dict | None) -> dict:
    return {
        "search_mode": _flag(flags, "search_mode", "dense"),
        "enable_hyde": bool(_flag(flags, "enable_hyde", False)),
        "enable_rerank": bool(_flag(flags, "enable_rerank", False)),
        "enable_crag": bool(_flag(flags, "enable_crag", settings.crag_enabled_by_default)),
        "enable_self_reflective": bool(_flag(flags, "enable_self_reflective", False)),
        "top_k": int(_flag(flags, "top_k", 5)),
    }





def run_rag_with_trace(
    question: str, flags: dict | int | None = None
) -> tuple[ChatResponse, list[RetrievedChunk]]:
    intent = classify_intent(question)
    if intent == "sql":
        response = _run_sql_inline(question)
        # Expose SQL rows as RetrievedChunks so eval can score them
        chunks = [
            RetrievedChunk(text=cp.text, source=cp.source, score=cp.score)
            for cp in response.metadata.retrieved_chunks
        ]
        return response, chunks
    if intent == "hybrid":
        return _run_hybrid_inline(question, flags if isinstance(flags, dict) else None)
    chunks = _retrieve(question, flags=flags if isinstance(flags, dict) else None)
    response = _generate(question, chunks, flags=flags if isinstance(flags, dict) else None)
    return response, chunks



run_rag_with_trace_no_cache = run_rag_with_trace
