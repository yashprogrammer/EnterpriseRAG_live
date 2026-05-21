from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


INTENT = Literal["rag", "sql", "hybrid", "web_fallback"]
FEATURE = Literal[
    "baseline",
    "sparse",
    "dense",
    "hybrid",
    "rerank",
    "hyde",
    "crag",
    "self_rag",
    "sql",
    "hybrid_rag_sql",
    "security",
    "wild",
]

class Golden(BaseModel):
    id: str = Field(..., pattern=r"^q-\d{3}$")
    question: str = Field(..., min_length=1)
    intent: INTENT
    golden_sources: list[str] = Field(..., min_length=1)
    golden_answer_keywords: list[str] = Field(..., min_length=1)
    demonstrates_feature: FEATURE
    expected_baseline: Literal["pass", "fail"]
    expected_with_feature: Literal["pass"]
    notes: str
    forbidden_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_expected_with_feature(self) -> "Golden":
        """Ensure expected_with_feature is always 'pass'."""
        if self.expected_with_feature != "pass":
            raise ValueError("expected_with_feature must be 'pass'")
        return self


def load_goldens(path: str | Path) -> list[Golden]:
    
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected YAML root to be a list, got {type(raw).__name__}")

    goldens = [Golden.model_validate(entry) for entry in raw]

    ids = [g.id for g in goldens]
    if len(ids) != len(set(ids)):
        duplicates = {i for i in ids if ids.count(i) > 1}
        raise ValueError(f"Duplicate golden IDs found: {duplicates}")

    # Warn (don't fail) if some feature categories have no entries.
    # Not every feature needs eval goldens — e.g. dense/sparse/security are
    # demo-only and intentionally excluded from the eval-progression set.
    present_features = {g.demonstrates_feature for g in goldens}
    all_features = set(FEATURE.__args__)  # type: ignore[attr-defined]
    missing = all_features - present_features
    if missing:
        import warnings
        warnings.warn(f"No golden entries for features: {missing} (OK if demo-only)")

    return goldens