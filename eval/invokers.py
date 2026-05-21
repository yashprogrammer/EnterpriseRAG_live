from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from app.config import settings
from app.models import ChatResponse, RetrievedChunk
from app.services.rag_service import run_rag_with_trace_no_cache



class SkippedIntent(Exception):
    pass

class Invoker(ABC):

    @abstractmethod
    def invoke(
        self, question: str, flags: dict, intent: str
    ) -> tuple[ChatResponse, list[RetrievedChunk]]:
        ...


class ServiceInvoker(Invoker):
    SUPPORTED_INTENTS = {"rag", "web_fallback"}

    def invoke(
        self, question: str, flags: dict, intent: str
    ) -> tuple[ChatResponse, list[RetrievedChunk]]:
        if intent not in self.SUPPORTED_INTENTS:
            raise SkippedIntent(f"intent={intent} not supported in service mode")

        if intent == "web_fallback" and not settings.tavily_api_key:
            raise SkippedIntent("tavily_unset: TAVILY_API_KEY not configured")

        return run_rag_with_trace_no_cache(question, flags)
