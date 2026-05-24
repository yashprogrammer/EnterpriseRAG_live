import logging

from app.config import settings
from app.models import RetrievedChunk

logger = logging.getLogger(__name__)

def search_web(query: str, max_results: int = 5) -> list[RetrievedChunk]:

    if not settings.tavily_api_key:
        raise ValueError("Tavily API key not configured")

    try:
        import tavily
        client = tavily.TavilyClient(api_key=settings.tavily_api_key)
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        results = response.get("results", [])
        return [
            RetrievedChunk(
                text=result["content"],
                source=result["url"],
                score=result.get("score", 0.0),
            )
            for result in results
        ]
    except Exception:
        logger.exception("Tavily web search failed")
        return []