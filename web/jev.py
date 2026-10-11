"""Presentation demo: selects approved UI options with native Jev System One.

Owner: Darren (approved isolated presentation router)
Diagram box: JEV UI decision engine
Contract: experimental mock Winston selector, not the banking intent contract
"""

import math
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import config

router = APIRouter(prefix="/api", tags=["presentation demo"])
Bundle = Literal[
    "transfer_editing",
    "recipient_check",
    "affordability_overview",
    "affordability_simulation",
    "investment_assessment",
    "multi_task_review",
]


class CurrentUI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: Bundle
    layout: Literal["focus", "split", "sequence", "canvas"]
    density: Literal["minimal", "standard", "detailed"]


class PresentationContext(BaseModel):
    """Structural mock Winston handoff; no prompts, values or task routing."""

    model_config = ConfigDict(extra="forbid")
    action_types: list[Literal["transfer", "bill_payment", "stock_order"]] = Field(
        min_length=1, max_length=4
    )
    complete: Literal[True]
    missing: list[str] = Field(max_length=0)
    objective: Literal["prepare", "assess_affordability", "check_recipient"]
    recipient_status: Literal["listed", "not_applicable"]
    available_context: list[
        Literal["account_balance", "projected_balance", "stock_quote", "holdings"]
    ] = Field(max_length=4)
    current_ui: CurrentUI | None = None

    @model_validator(mode="after")
    def check_context(self):
        if len(set(self.available_context)) != len(self.available_context):
            raise ValueError("Duplicate context")
        if self.objective == "check_recipient" and "transfer" not in self.action_types:
            raise ValueError("Recipient checking requires a transfer")
        if ("transfer" in self.action_types) != (self.recipient_status == "listed"):
            raise ValueError("Recipient status does not match task")
        if self.objective == "assess_affordability" and not {
            "account_balance",
            "projected_balance",
        }.issubset(self.available_context):
            raise ValueError("Affordability requires balance and projection context")
        if "stock_order" in self.action_types and "stock_quote" not in self.available_context:
            raise ValueError("Stock presentation requires quote context")
        return self


def build_request(context: PresentationContext) -> dict[str, Any]:
    """Offer coherent bundles from an already resolved objective; one native request."""
    multiple = len(context.action_types) > 1
    decision = context.objective == "assess_affordability"
    if decision:
        bundles = ["affordability_overview", "affordability_simulation"]
    elif context.objective == "check_recipient":
        bundles = ["recipient_check"]
    elif multiple:
        bundles = ["multi_task_review"] + (
            ["investment_assessment"] if "stock_order" in context.action_types else []
        )
    elif context.action_types[0] == "transfer":
        bundles = ["transfer_editing", "recipient_check"]
    else:
        bundles = [
            "investment_assessment"
            if context.action_types[0] == "stock_order"
            else "multi_task_review"
        ]
    layouts = ["split", "sequence" if multiple else "focus"] + (["canvas"] if decision else [])
    descriptions = {
        "transfer_editing": "Editable transfer and funding account; compact account context.",
        "recipient_check": (
            "Prioritize listed recipient identity beside the editable transfer. "
            "Never claim bank verification."
        ),
        "affordability_overview": (
            "Prioritize projected remaining balances and editable tasks; no sliders."
        ),
        "affordability_simulation": (
            "Projected balances, per-task adjustment sliders, funding controls "
            "and estimates together."
        ),
        "investment_assessment": (
            "Stock quantity, quote estimate, holdings and price uncertainty; "
            "preserve all other tasks."
        ),
        "multi_task_review": (
            "All editable tasks with funding context and one explicit review flow."
        ),
        "split": "Tasks beside shared context within one integrated plan.",
        "sequence": "All tasks in a coordinated numbered sequence.",
        "focus": "One focused editable task with compact shared context.",
        "canvas": "Give financial projections priority beside editable tasks.",
        "minimal": "Compact spacing.",
        "standard": "Balanced fields and linked estimates.",
        "detailed": "Expanded estimate arrangement and financial context.",
    }
    options = {"bundle": bundles, "layout": layouts, "density": ["minimal", "standard", "detailed"]}
    if multiple:
        options["primary"] = [str(i) for i in range(len(context.action_types))]
    return {
        "model": config.MAKERS_JEV_MODEL,
        "state": {
            "source": "mock_winston_output",
            **context.model_dump(),
            "rules": (
                "Winston resolved the tasks, objective and ambiguity. Select "
                "presentation only. Preserve all fields and mandatory review. Data "
                "is mock; currencies remain separate."
            ),
        },
        "questions": {
            name: {
                "type": "choice",
                "instructions": (
                    f"Choose the best {name} for Winston's supplied objective. Consider "
                    f"the current UI; avoid unnecessary rearrangement. Select only an "
                    f"offered option."
                ),
                "criteria": {
                    option: descriptions.get(
                        option, f"Prioritize action {option} without changing execution order."
                    )
                    for option in choices
                },
            }
            for name, choices in options.items()
        },
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
    bundle = picks["bundle"]
    state = payload["state"]
    types = state["action_types"]
    affordability = bundle.startswith("affordability_")
    support = (
        ["accounts", "projection"]
        if affordability
        else ["recipient"]
        if bundle == "recipient_check"
        else ["accounts", "holdings"]
        if bundle == "investment_assessment"
        else ["accounts"]
    )
    selection = {
        "bundle": bundle,
        "layout": picks["layout"],
        "density": picks["density"],
        "primary": int(picks.get("primary", "0")),
        "interaction": "simulate"
        if bundle == "affordability_simulation"
        else "explore"
        if affordability
        else "execute",
        "focus": "affordability"
        if affordability
        else "recipient"
        if bundle == "recipient_check"
        else "risk"
        if bundle == "investment_assessment"
        else "amount",
        "strategy": "multi" if len(types) > 1 else "focused",
        "supporting": support,
        "tools": ["form", "slider", "chart"]
        if bundle == "affordability_simulation"
        else ["form", "chart"]
        if affordability
        else ["form"],
    }
    model = value.get("model")
    if not isinstance(model, str) or not model.startswith("jev-"):
        raise ValueError("Unexpected model identity")
    return {
        "source": "jev",
        "selection": selection,
        "systemone": {"model": model, "answers": answers},
    }


@router.post("/presentation-demo")
async def compose_demo(context: PresentationContext) -> dict[str, Any]:
    """Call live JEV for an explicit mock output; never create or execute a draft."""
    if not config.MAKERS_MODELS_KEY:
        raise HTTPException(502, "Makers Models key is not configured on the server.")
    payload = build_request(context)
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
