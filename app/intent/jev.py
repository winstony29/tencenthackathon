"""Jev client: the only file that calls TypeSafe's API.

Owner: Winston
Diagram box: Jev × K phrasings (Intent detection column)
Contract: none; called by app/intent/router.py; gateway settings in D-005
"""

import asyncio
import math
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException

from app import config


@dataclass(frozen=True)
class Question:
    name: str
    instructions: str
    criteria: dict[str, str]  # option_id -> what choosing it means


@dataclass(frozen=True)
class Answer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class Reply:
    model: str
    answers: dict[str, Answer]


async def ask(text: str, questions: list[Question], context: dict[str, Any] | None = None) -> Reply:
    """Ask Jev every question about one text in a single System One call.

    `context` adds facts to Jev's state, such as the tasks a follow-up refers to.
    Raises HTTP 502 when the key is missing, the gateway fails, or the reply
    picks anything that was not offered.
    """
    if not config.MAKERS_MODELS_KEY:
        raise HTTPException(502, "Jev is not configured on the server.")
    payload = {
        "model": config.MAKERS_JEV_MODEL,
        "state": {"text": text, **(context or {})},
        "questions": {
            q.name: {"type": "choice", "instructions": q.instructions, "criteria": q.criteria}
            for q in questions
        },
    }
    try:
        async with httpx.AsyncClient(timeout=config.MAKERS_JEV_TIMEOUT_SECONDS) as client:
            response = await client.post(
                config.MAKERS_JEV_ENDPOINT,
                headers={"Authorization": f"Bearer {config.MAKERS_MODELS_KEY}"},
                json=payload,
            )
        response.raise_for_status()
        return parse(response.json(), questions)
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise HTTPException(502, "Jev is unavailable right now. Please try again.") from error


async def ask_all(
    texts: list[str], questions: list[Question], context: dict[str, Any] | None = None
) -> list[Reply]:
    """Ask the same questions about every phrasing at once; replies keep the order of texts."""
    return list(await asyncio.gather(*(ask(text, questions, context) for text in texts)))


def parse(value: Any, questions: list[Question]) -> Reply:
    """Turn a System One reply into answers, refusing anything outside the offered options."""
    answers = value.get("answers") if isinstance(value, dict) else None
    model = value.get("model") if isinstance(value, dict) else None
    if not isinstance(answers, dict) or not isinstance(model, str):
        raise ValueError("reply has no answers or model")
    return Reply(model=model, answers={q.name: _answer(answers.get(q.name), q) for q in questions})


def _answer(raw: Any, question: Question) -> Answer:
    # Jev may only pick: the choice and every probability must be for an offered option.
    if not isinstance(raw, dict) or raw.get("choice") not in question.criteria:
        raise ValueError(f"{question.name}: missing or unoffered choice")
    probabilities = raw.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(question.criteria):
        raise ValueError(f"{question.name}: probabilities do not match the options")
    numbers = [raw.get("confidence"), *probabilities.values()]
    if not all(_is_probability(n) for n in numbers):
        raise ValueError(f"{question.name}: probability outside 0..1")
    return Answer(
        raw["choice"], float(raw["confidence"]), {k: float(v) for k, v in probabilities.items()}
    )


def _is_probability(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value) and 0 <= value <= 1
