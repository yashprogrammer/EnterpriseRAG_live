import asyncio
from typing import Any

from fastapi import APIRouter
from loguru import logger

from app.config import settings


router = APIRouter(tags=["admin"])

async def _ping_postgres() -> bool:
    try:
        import psycopg2

        conn = psycopg2.connect(settings.database_url, connect_timeout=2)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return True
    except Exception as exc:
        logger.debug("Postgres health check failed: {}", exc)
        return False

async def _ping_qdrant() -> bool:
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=settings.qdrant_url, timeout=2)
        client.get_collections()
        return True
    except Exception as exc:
        logger.debug("Qdrant health check failed: {}", exc)
        return False

async def _ping_redis() -> bool:
    try:
        from upstash_redis import Redis

        redis = Redis(url=settings.upstash_redis_url, token=settings.upstash_redis_token)
        redis.ping()
        return True
    except Exception as exc:
        logger.debug("Redis health check failed: {}", exc)
        return False

async def _ping_openai() -> bool:
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        await client.models.list()
        return True
    except Exception as exc:
        logger.debug("OpenAI health check failed: {}", exc)
        return False


@router.get("/admin/health")
async def health_check() -> dict[str, Any]:
    
    results = await asyncio.gather(
        _ping_postgres(),
        _ping_qdrant(),
        _ping_redis(),
        _ping_openai(),
        return_exceptions=True,
    )
    postgres_ok = bool(results[0]) if not isinstance(results[0], Exception) else False
    qdrant_ok = bool(results[1]) if not isinstance(results[1], Exception) else False
    redis_ok = bool(results[2]) if not isinstance(results[2], Exception) else False
    openai_ok = bool(results[3]) if not isinstance(results[3], Exception) else False

    all_ok = postgres_ok and qdrant_ok and redis_ok and openai_ok
    status = "ok" if all_ok else "degraded"

    return {
        "status": status,
        "qdrant": qdrant_ok,
        "postgres": postgres_ok,
        "redis": redis_ok,
        "openai": openai_ok,
    }