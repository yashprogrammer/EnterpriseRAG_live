
import json
import logging

from app.config import settings
from app.models import ReflectionResult
from app.services.llm_service import generate_with_json


_REFLECTION_PROMPT = """You are a strict reviewer evaluating an AI-generated answer.
Your job is to FAIL answers that don't deliver real value to the user.

User Question: {question}

Generated Answer: {answer}

Retrieved Context (if any): {context}

Score each criterion 1-10. BE STRICT — do not give pity points.

1. Relevance (1-10):
   - 10: Directly answers the question with concrete content.
   - 5 or below: Hedges, refuses, or gives only meta-commentary
     ("The retrieved context does not provide...", "I don't have information...",
     "It depends...", "Many things could be meant by this...").
   - 3 or below: Off-topic or talks about unrelated subjects from the context.

2. Accuracy (1-10):
   - 10: All claims are grounded in the retrieved context and are factually correct.
   - 5 or below: Some claims are unsupported by the context.
   - 3 or below: The answer contradicts the context or invents facts.

3. Completeness (1-10):
   - 10: Addresses every part of the question with sufficient depth.
   - 5 or below: Misses major parts of the question, or is too short to be useful
     (e.g. one sentence answers to a multi-part technical question).
   - 3 or below: Effectively a non-answer ("I don't know", "see the documentation").

4. Clarity (1-10):
   - 10: Well-structured, easy to follow, uses examples where helpful.
   - 5 or below: Disorganised, jargon-heavy, or confusing.

Overall reflection_score = average of the four criteria / 10.0 (range 0.0–1.0).

needs_regeneration is True if ANY of the following hold:
- The answer is a hedge or refusal ("does not provide", "I don't have", "unclear").
- Relevance or Completeness scored 5 or below.
- The overall score is below 0.85.
- The question is vague/ambiguous and a SHARPER reformulation would clearly help.

When needs_regeneration is True, set refined_question to a fully self-contained,
single-sentence reformulation of the user's question that adds the missing
specificity (e.g. add domain context, name the entity, narrow the timeframe).

Respond ONLY with a JSON object — no prose before or after:
{{"reflection_score": float, "needs_regeneration": bool, "refined_question": str, "reasoning": str}}
"""


def reflect_on_answer(
    question: str,
    answer: str,
    context: str = "",
) -> ReflectionResult:
    try:
        formatted_prompt = _REFLECTION_PROMPT.format(
            question=question,
            answer=answer,
            context=context,
        )
        response = generate_with_json(
            system_prompt=formatted_prompt,
            user_message="",
            model=settings.llm_model_grader,
            temperature=0.0,
        )
        parsed = json.loads(response["text"])
        return ReflectionResult(**parsed)
    except Exception as exc:
        logging.error("Reflection failed: %s", exc)
        return ReflectionResult(
            reflection_score=1.0,
            needs_regeneration=False,
            refined_question="",
            reasoning="Reflection failed, accepting answer as-is",
        )


def should_regenerate(reflection: ReflectionResult, iteration: int) -> bool:
    return (
        reflection.needs_regeneration
        and reflection.reflection_score < settings.reflection_min_score
        and iteration < settings.max_reflection_retries
    )
