"""Presentation demo: selects approved UI options with native Jev System One.

Owner: Darren (approved isolated presentation router)
Diagram box: JEV UI decision engine
Contract: experimental mock Winston selector, not the banking intent contract
"""

import math
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException

from app import config

router = APIRouter(prefix="/api", tags=["presentation demo"])
Fixture = Literal["transfer", "partial", "multi", "decision"]


def build_request(fixture: Fixture) -> dict[str, Any]:
    """Build only eligible choices from known structural demo outputs, with no financial values."""
    partial = fixture == "partial"
    decision = fixture == "decision"
    multiple = fixture in ("multi", "decision")
    options = {
        "layout": ["focus"]
        if partial
        else ["split", "sequence" if multiple else "focus", "comparison"]
        + (["canvas"] if decision else []),
        "interaction": ["clarify"]
        if partial
        else ["simulate", "compare", "explore"]
        if decision
        else ["execute", "explore", "compare"],
        "focus": ["recipient"]
        if partial
        else ["affordability", "amount", "risk"]
        if decision
        else ["amount", "risk"]
        if multiple
        else ["amount", "recipient"],
        "density": ["minimal", "standard", "detailed"],
        "strategy": ["partial" if partial else "multi" if multiple else "focused"],
        "primary": [str(index) for index in range(2 if multiple else 1)],
    }
    descriptions = {
        "focus": "One focused editable banking task.",
        "split": "Task forms beside relevant context.",
        "sequence": "Multiple task forms shown as one coordinated workspace.",
        "comparison": "Task forms with account comparison context; currencies stay separate.",
        "canvas": "Financial decision workspace with projections given visual priority.",
        "execute": "Complete task ready for user editing and later review, never execute money.",
        "clarify": "Ask for missing or ambiguous details while keeping known task fields.",
        "simulate": "Let the user adjust amounts or quantities to inspect affordability.",
        "compare": "Compare account context while keeping currencies separate.",
        "explore": "Inspect relevant context alongside editable tasks.",
        "amount": "Emphasize the editable amount or quantity.",
        "recipient": "Emphasize recipient clarification and verification.",
        "affordability": "Emphasize projected remaining account balances.",
        "risk": "Emphasize stock price uncertainty and estimate limitations.",
        "minimal": "Compact presentation for one missing detail.",
        "standard": "Balanced forms and context.",
        "detailed": "More supporting explanation for a financial decision.",
        "partial": "Incomplete transfer requiring clarification.",
        "focused": "One complete task.",
        "multi": "Multiple related tasks, all of which must stay visible.",
    }
    questions = {
        name: {
            "type": "choice",
            "instructions": (
                f"Select the best {name} for the next interaction from the workflow state. "
                "Select only an offered option."
            ),
            "criteria": {
                option: descriptions.get(
                    option,
                    f"Give action {int(option) + 1} primary visual priority."
                    if option.isdigit()
                    else option,
                )
                for option in choices
            },
        }
        for name, choices in options.items()
    }
    support = (
        ["accounts", "recipient"]
        if not multiple
        else ["accounts", "holdings"] + (["projection"] if decision else [])
    )
    tools = (
        ["form"]
        if partial
        else ["form", "slider", "comparison", "chart"]
        if decision
        else ["form", "comparison"]
    )
    for name, choices in (
        ("support", support),
        ("tool", [tool for tool in tools if tool != "form"]),
    ):
        for option in choices:
            questions[f"{name}_{option}"] = {
                "type": "choice",
                "instructions": (
                    f"Should the workspace include {option}? Include if useful to the next step. "
                    "Projection supports affordability; sliders and charts help simulation; "
                    "recipient context supports transfer verification."
                ),
                "criteria": {
                    "include": f"Include the approved {option} component.",
                    "omit": f"Omit optional {option} context.",
                },
            }
    return {
        "model": config.MAKERS_JEV_MODEL,
        "state": {
            "source": "mock_winston_output",
            "action_types": ["bill_payment", "stock_order"] if multiple else ["transfer"],
            "complete": not partial,
            "missing": ["recipient", "amount"] if partial else [],
            "user_need": "assess_affordability"
            if decision
            else "clarify_transfer"
            if partial
            else "review_tasks",
            "banking_context": (
                "Mock accounts, listed recipients, holdings and stock quote; no schedules. "
                "Preserve every task and mandatory review."
            ),
        },
        "questions": questions,
    }


def parse_response(value: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Check every answer against the offered criteria before returning a presentation selection."""
    if not isinstance(value, dict) or not isinstance(value.get("answers"), dict):
        raise ValueError("Missing System One answers")
    picks = {}
    answers = {}
    for name, question in payload["questions"].items():
        answer = value["answers"].get(name)
        if (
            not isinstance(answer, dict)
            or answer.get("type") != "choice"
            or answer.get("choice") not in question["criteria"]
        ):
            raise ValueError("Invalid or missing choice")
        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")
        if (
            not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("Invalid confidence")
        if (
            not isinstance(probabilities, dict)
            or set(probabilities) != set(question["criteria"])
            or any(
                not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1
                for p in probabilities.values()
            )
        ):
            raise ValueError("Invalid probabilities")
        picks[name] = answer["choice"]
        answers[name] = {
            "type": "choice",
            "choice": answer["choice"],
            "confidence": confidence,
            "probabilities": probabilities,
        }
    selection = {
        name: picks[name] for name in ("layout", "interaction", "focus", "density", "strategy")
    }
    selection.update(
        primary=int(picks["primary"]),
        supporting=[
            name.removeprefix("support_")
            for name, pick in picks.items()
            if name.startswith("support_") and pick == "include"
        ],
        tools=["form"]
        + [
            name.removeprefix("tool_")
            for name, pick in picks.items()
            if name.startswith("tool_") and pick == "include"
        ],
    )
    model = value.get("model")
    if not isinstance(model, str) or not model.startswith("jev-"):
        raise ValueError("Unexpected model identity")
    return {
        "source": "jev",
        "selection": selection,
        "systemone": {"model": model, "answers": answers},
    }


@router.post("/presentation-demo")
async def compose_demo(fixture: Fixture) -> dict[str, Any]:
    """Call live JEV for an explicit mock output; never create or execute a draft."""
    if not config.MAKERS_MODELS_KEY:
        raise HTTPException(502, "Makers Models key is not configured on the server.")
    payload = build_request(fixture)
    try:
        async with httpx.AsyncClient(timeout=config.MAKERS_JEV_TIMEOUT_SECONDS) as client:
            response = await client.post(
                config.MAKERS_JEV_ENDPOINT,
                headers={"Authorization": f"Bearer {config.MAKERS_MODELS_KEY}"},
                json=payload,
            )
        if response.status_code != 200:
            raise HTTPException(
                502, f"JEV gateway returned HTTP {response.status_code}; using local fallback."
            )
        return parse_response(response.json(), payload)
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise HTTPException(
            502, "JEV response unavailable or invalid; using local fallback."
        ) from error
