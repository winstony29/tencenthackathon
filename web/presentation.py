"""V3 presentation-only component planner. Owner: Darren; no banking operations."""

import json
import math
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import config

router = APIRouter()
CATALOG = json.loads(Path(__file__).with_name("presentation-catalog.json").read_text("utf-8"))


class CurrentUI(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str = Field(max_length=40)
    components: list[str] = Field(max_length=20)
    layout: Literal["focus", "split", "sequence"]
    density: Literal["compact", "comfortable"]


class ComponentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_types: list[Literal["transfer", "bill_payment", "stock_order"]] = Field(
        min_length=1, max_length=4
    )
    complete: Literal[True]
    missing: list[str] = Field(max_length=0)
    objective: Literal["prepare", "assess_affordability", "simulate", "check_recipient", "review"]
    recipient_status: Literal["listed", "not_applicable"]
    available_context: list[
        Literal[
            "accounts",
            "recipients",
            "billers",
            "quote",
            "holdings",
            "projection",
            "multiple",
            "alternatives",
        ]
    ] = Field(max_length=8)
    viewport: Literal["mobile", "desktop"] = "desktop"
    current_ui: CurrentUI | None = None

    @model_validator(mode="after")
    def coherent(self):
        data = self.available_context
        if len(set(data)) != len(data) or "accounts" not in data:
            raise ValueError("Invalid data categories")
        if ("transfer" in self.action_types) != (self.recipient_status == "listed"):
            raise ValueError("Invalid recipient status")
        for action, dependency in [
            ("transfer", "recipients"),
            ("bill_payment", "billers"),
            ("stock_order", "quote"),
        ]:
            if action in self.action_types and dependency not in data:
                raise ValueError("Missing task data")
        if self.objective in ("assess_affordability", "simulate") and "projection" not in data:
            raise ValueError("Missing projection data")
        if self.objective == "check_recipient" and "transfer" not in self.action_types:
            raise ValueError("No recipient to inspect")
        if (len(self.action_types) > 1) != ("multiple" in data):
            raise ValueError("Invalid multi-task state")
        if self.current_ui:
            keys = {c["key"] for c in eligible(self)}
            if self.current_ui.primary not in keys or not set(self.current_ui.components).issubset(
                keys
            ):
                raise ValueError("Invalid current UI")
        return self


def eligible(context: ComponentContext):
    items = []
    for component, spec in CATALOG.items():
        if context.objective not in spec["objectives"] or not set(spec["data"]).issubset(
            context.available_context
        ):
            continue
        actions = [i for i, t in enumerate(context.action_types) if t in spec["types"]]
        if not actions:
            continue
        for index in actions if spec["perAction"] else [None]:
            items.append(
                {
                    "key": component + (f":{index}" if index is not None else ""),
                    "component": component,
                    "primary": context.objective in spec["primaryFor"],
                    "required": context.objective in spec["requiredFor"],
                }
            )
    return items


def build_request(context: ComponentContext):
    items = eligible(context)
    options = {
        "primary": [c["key"] for c in items if c["primary"]],
        "layout": ["split", "sequence"] if len(context.action_types) > 1 else ["focus", "split"],
        "density": ["compact", "comfortable"],
        "interaction": ["review"]
        if context.objective == "review"
        else ["adjust"]
        if context.objective == "simulate"
        else ["edit", "inspect"]
        if context.objective == "prepare"
        else ["inspect"],
        "continuity": ["preserve", "recompose"] if context.current_ui else ["recompose"],
    }
    options.update({f"show_{c['key']}": ["include", "omit"] for c in items if not c["required"]})
    descriptions = {
        c["key"]: CATALOG[c["component"]]["title"] + ". " + CATALOG[c["component"]]["userAction"]
        for c in items
    }
    descriptions.update(
        {
            "focus": "One dominant component with compact support below.",
            "split": "Primary component beside supporting editable tasks.",
            "sequence": "Coordinated tasks with the primary component first.",
            "compact": "Tight but readable spacing.",
            "comfortable": "More space around the main task.",
            "include": "Include this useful supporting component.",
            "omit": "Omit this optional component to avoid clutter.",
            "preserve": "Keep the current eligible composition for this unchanged objective.",
            "recompose": "Choose a fresh composition for the supplied objective.",
            "edit": "Emphasize editable controls.",
            "inspect": "Emphasize analysis while retaining required controls.",
            "adjust": "Emphasize separate hypothetical controls, never commit silently.",
            "review": "Read committed details and explicitly confirm the demo.",
        }
    )
    return {
        "model": config.MAKERS_JEV_MODEL,
        "state": {
            "source": "mock_winston_output",
            **context.model_dump(),
            "rules": (
                "Winston resolved the objective and ambiguity. Choose presentation only. "
                "Independent questions; required components are enforced by code. "
                "No financial values or authorization. Prefer useful context with minimal clutter. "
                "Prefer a focused or sequential layout on mobile. Select at most two optional "
                "supporting panels on desktop and one on mobile. Preserve an eligible current "
                "composition unless a change clearly improves the supplied objective."
            ),
        },
        "questions": {
            name: {
                "type": "choice",
                "instructions": (
                    f"Choose {name} for the supplied objective. "
                    + (
                        descriptions[name.removeprefix("show_")] + " "
                        if name.startswith("show_")
                        else ""
                    )
                    + "Questions independently see the same state. Select only an offered option."
                ),
                "criteria": {option: descriptions[option] for option in choices},
            }
            for name, choices in options.items()
        },
    }


def parse_response(value: Any, payload: dict, latency_ms: float = 0):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("model"), str)
        or not value["model"].startswith("jev-")
    ):
        raise ValueError("Unexpected model")
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(payload["questions"]):
        raise ValueError("Unexpected answers")
    for name, question in payload["questions"].items():
        answer = answers[name]
        if (
            not isinstance(answer, dict)
            or answer.get("type") != "choice"
            or answer.get("choice") not in question["criteria"]
        ):
            raise ValueError("Invalid choice")
        ps = answer.get("probabilities")
        if not isinstance(ps, dict) or set(ps) != set(question["criteria"]):
            raise ValueError("Invalid probabilities")
        for n in [answer.get("confidence"), *ps.values()]:
            if (
                isinstance(n, bool)
                or not isinstance(n, (int, float))
                or not math.isfinite(n)
                or not 0 <= n <= 1
            ):
                raise ValueError("Invalid probability")
        if abs(sum(ps.values()) - 1) > 0.05:
            raise ValueError("Invalid probability total")
    return {
        "source": "jev",
        "model": value["model"],
        "answers": {
            k: {field: v[field] for field in ("type", "choice", "confidence", "probabilities")}
            for k, v in answers.items()
        },
        "latency_ms": round(latency_ms),
    }


@router.post("/presentation-v3")
async def present(context: ComponentContext):
    if not config.MAKERS_MODELS_KEY:
        raise HTTPException(502, "Live composition unavailable.")
    payload = build_request(context)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=config.MAKERS_JEV_TIMEOUT_SECONDS) as client:
            response = await client.post(
                config.MAKERS_JEV_ENDPOINT,
                headers={"Authorization": f"Bearer {config.MAKERS_MODELS_KEY}"},
                json=payload,
            )
        response.raise_for_status()
        return parse_response(response.json(), payload, (time.perf_counter() - started) * 1000)
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise HTTPException(502, "Live composition unavailable; local layout ready.") from error
