import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator

class ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="User message to the AI assistant",
    )
    @field_validator("message")
    @classmethod
    def validate_message_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty or whitespace only")
        injection_patterns = [
            r"(?i)(ignore\s+previous|ignore\s+above|forget\s+your\s+instructions)",
            r"(?i)(system\s*prompt|reveal\s+your\s+instructions|show\s+your\s+prompt)",
            r"(?i)(you\s+are\s+now|new\s+instructions|override\s+previous)",
            r"(?i)(<\s*script|javascript:|on\w+\s*=)",
        ]

        for pattern in injection_patterns:
            if re.search(pattern, v):
                raise ValueError("Message contains potentially malicious content")

        if re.match(r"^[\W_]+$", v):
            raise ValueError("Message must contain actual text content")

        return v


class RetrievedChunkPreview(BaseModel):
    text: str
    source: str
    score: float = 0.0

class ResponseMetadata(BaseModel):
    route: str = "rag"
    retrieved_chunks: list[RetrievedChunkPreview] = Field(default_factory=list)

class ChatResponse(BaseModel):
    answer: str = Field(..., min_length=0)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: ResponseMetadata = Field(default_factory=ResponseMetadata)

class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="User question",
    )
    enable_rerank: bool = False
    top_k: int = Field(default=5, ge=1, le=50)
    search_mode: Literal["dense", "sparse", "hybrid"] = "dense"
    @field_validator("question")
    @classmethod
    def validate_question_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Question cannot be empty or whitespace only")

        injection_patterns = [
            r"(?i)(ignore\s+previous|ignore\s+above|forget\s+your\s+instructions)",
            r"(?i)(system\s*prompt|reveal\s+your\s+instructions|show\s+your\s+prompt)",
            r"(?i)(you\s+are\s+now|new\s+instructions|override\s+previous)",
            r"(?i)(<\s*script|javascript:|on\w+\s*=)",
        ]
        for pattern in injection_patterns:
            if re.search(pattern, v):
                raise ValueError("Question contains potentially malicious content")

        if re.match(r"^[\W_]+$", v):
            raise ValueError("Question must contain actual text content")

        return v


class RetrievedChunk(BaseModel):
    text: str
    source: str
    score: float = 0.0