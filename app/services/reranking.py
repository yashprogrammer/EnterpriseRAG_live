import logging
from typing import cast

from app.config import settings
from app.models import RetrievedChunk

logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self) -> None:
        self.backend = settings.reranker_backend
        self._local_model: object | None = None
        self._voyage_client: object | None = None

    def _load_local_model(self) -> object:
        if self._local_model is None:
            from sentence_transformers import CrossEncoder

            self._local_model = CrossEncoder(settings.reranker_model)
        return self._local_model

    def _load_voyage_client(self) -> object:
        if self._voyage_client is None:
            import voyageai

            if not settings.voyage_api_key:
                raise ValueError("Voyage API key is required for voyage reranker backend")
            self._voyage_client = voyageai.Client(api_key=settings.voyage_api_key)
        return self._voyage_client

    
    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []
        
        top_k = top_k or settings.reranker_initial_top_k
        top_k = min(top_k, len(chunks))

        try:
            if self.backend == "voyage":
                return self._rerank_voyage(query, chunks, top_k)
            return self._rerank_local(query, chunks, top_k)
        except Exception:
            logger.exception("Reranking failed, returning original order")
            return chunks[:top_k]


    def _rerank_local(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        model = self._load_local_model()
        pairs = [[query, chunk.text] for chunk in chunks]
        scores = cast("list[float]", model.predict(pairs))

        scored = [
            RetrievedChunk(text=chunk.text, source=chunk.source, score=float(score))
            for chunk, score in zip(chunks, scores, strict=True)
        ]
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:top_k]


    def _rerank_voyage(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        client = self._load_voyage_client()
        documents = [chunk.text for chunk in chunks]
        result = client.rerank(
            query=query,
            documents=documents,
            model=settings.voyage_model,
            top_k=top_k,
        )

        # Map results back to RetrievedChunk
        reranked: list[RetrievedChunk] = []
        for item in result.results:
            idx = item.index
            chunk = chunks[idx]
            reranked.append(
                RetrievedChunk(
                    text=chunk.text,
                    source=chunk.source,
                    score=float(item.relevance_score),
                )
            )
        return reranked
