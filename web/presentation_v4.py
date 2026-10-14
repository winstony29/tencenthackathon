"""V4 bounded evaluator for json-render; presentation choices only. Owner: Darren."""

import json
import math
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app import config, evidence
from app.bank.readonly import get_account_insights
from app.contracts import EnquiryPresentationRequest

_client: httpx.AsyncClient | None = None


async def open_connection() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=config.MAKERS_JEV_TIMEOUT_SECONDS)


async def close_connection() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


router = APIRouter()
CATALOG = json.loads(Path(__file__).with_name("v4-catalog.json").read_text("utf-8"))
OPTIONAL = {"RecipientDetails", "StockEstimate", "FundingOptions", "AccountInsights"}


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: Literal["prepare", "assess_affordability", "simulate", "review"]
    action_types: list[
        Literal["transfer", "bill_payment", "stock_order", "account_transfer", "fx_conversion"]
    ] = Field(min_length=1, max_length=4)
    financial_context: bool = False
    funding: Literal["sufficient", "shortfall", "unknown"] = "unknown"
    commitments: Literal["present", "none", "unknown"] = "unknown"
    reserve: Literal["retained", "breached", "unknown"] = "unknown"
    alternatives: bool
    viewport: Literal["mobile", "desktop"]


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: Context
    selected: list[str] = Field(default_factory=list, max_length=24)
    questions: dict[str, list[str]] = Field(min_length=1, max_length=48)


def recipes(context: Context) -> list[dict[str, Any]]:
    result = []
    for component, d in CATALOG["components"].items():
        if context.objective not in d["objectives"] or (
            d.get("alternatives") and not context.alternatives
        ):
            continue
        if d.get("financial") and not context.financial_context:
            continue
        actions = (
            [
                i
                for i, t in enumerate(context.action_types)
                if t in d.get("types", context.action_types)
            ]
            if d.get("perAction")
            else [None]
        )
        for action in actions:
            for variant, description in d["variants"].items():
                if variant == "flow" and (
                    action is None or context.action_types[action] != "transfer"
                ):
                    continue
                if variant == "funding-gap" and context.funding != "shortfall":
                    continue
                if component == "Workspace" and context.viewport == "mobile" and variant == "split":
                    continue
                suffix = "all" if action is None else str(action)
                task = (
                    ""
                    if action is None
                    else f" for task {action + 1} ({context.action_types[action]})"
                )
                result.append(
                    {
                        "id": f"{component}_{suffix}_{variant}",
                        "component": component,
                        "action": action,
                        "resource": f"{component}_{suffix}",
                        "slots": d.get("slots", []),
                        "description": f"{component}{task}: {d['description']} {description}",
                    }
                )
    return result


def build_request(body: EvaluationRequest) -> dict[str, Any]:
    """Validate upstream choice IDs; supply only server-owned state and wording.

    The upstream composer owns selection and tree construction. This adapter narrows
    its offered choices to banking eligibility and never accepts client prose/values.
    """
    pool = recipes(body.context)
    by_id = {r["id"]: r for r in pool}
    expected = {}
    native = {}

    def question(name: str, offered: Any, allowed: dict, instructions: str) -> None:
        expected[name] = set(offered)
        if not allowed:
            raise ValueError("No eligible placement")
        native[name] = {"type": "choice", "instructions": instructions, "criteria": allowed}

    context = body.context.model_dump()
    guidance = CATALOG["prompts"][body.context.objective]
    state = {"user_request": guidance, "context": context}
    if not body.selected:
        roots = {r["id"]: r["description"] for r in pool if r["component"] == "Workspace"}
        question(
            "root",
            [*roots, "unavailable"],
            roots,
            "Choose the best approved outer layout for this resolved objective and viewport.",
        )
        groups = {}
        for recipe in pool:
            groups.setdefault(recipe["resource"], []).append(recipe)
        for i, group in enumerate(groups.values()):
            choices = {
                "omit": "Omit this optional context.",
                **{f"use:{r['id']}": r["description"] for r in group},
            }
            allowed = (
                choices
                if group[0]["component"] in OPTIONAL or group[0]["component"] == "Workspace"
                else {k: v for k, v in choices.items() if k != "omit"}
            )
            question(
                f"select_{i}",
                choices,
                allowed,
                "Choose one variant. Keep required task content. Include optional context only "
                "if useful and non-redundant; at most one optional component on mobile "
                "or two on desktop.",
            )
        state["capabilities"] = [{"id": r["id"], "description": r["description"]} for r in pool]
    else:
        if any(i not in by_id for i in body.selected) or len(set(body.selected)) != len(
            body.selected
        ):
            raise ValueError("Invalid selected recipes")
        selected = [by_id[i] for i in body.selected]
        if selected[0]["component"] != "Workspace" or any(
            r["component"] == "Workspace" for r in selected[1:]
        ):
            raise ValueError("Invalid selected root")
        if len({r["resource"] for r in selected}) != len(selected):
            raise ValueError("Duplicate resource")
        destinations = {
            f"node_{i}:{slot}": (i, slot) for i, r in enumerate(selected) for slot in r["slots"]
        }
        for i, r in enumerate(selected[1:], 1):
            parents = {k: v for k, v in destinations.items() if v[0] != i}
            allowed = {}
            for key, (parent, slot) in parents.items():
                p = selected[parent]
                component = r["component"]
                if component in {"TaskEditor", "StockEstimate", "RecipientDetails"}:
                    valid = (
                        p["component"] == "TaskGroup"
                        and p["action"] == r["action"]
                        and slot == "details"
                    )
                elif component in {"BalanceProjection", "AmountSimulator"}:
                    valid = p["component"] == "DecisionGroup" and slot == (
                        "outcome" if component == "BalanceProjection" else "controls"
                    )
                else:
                    target = (
                        "context"
                        if component in {"FundingOptions", "AccountInsights"}
                        else "primary"
                    )
                    if component == "TaskGroup":
                        target = (
                            "primary"
                            if body.context.objective == "prepare" and r["action"] == 0
                            else "tasks"
                        )
                    valid = parent == 0 and slot == target
                if valid:
                    allowed[key] = f"{p['description']} Slot: {slot}."
            if len(parents) > 1:
                question(
                    f"parent_node_{i}",
                    parents,
                    allowed,
                    "Choose an eligible parent and slot. Keep controls with their task or outcome.",
                )
            positions = {str(n): f"Position {n} among siblings." for n in range(1, len(selected))}
            question(
                f"order_node_{i}",
                positions,
                positions,
                "Choose reading order: editor before its support; controls before outcome. "
                "Keep all task content.",
            )
        state["selected_elements"] = [
            {"id": f"node_{i}", "type": r["component"], "content": r["description"]}
            for i, r in enumerate(selected)
        ]
    if set(body.questions) != set(expected) or any(
        len(v) != len(set(v)) or set(v) != expected[k] for k, v in body.questions.items()
    ):
        raise ValueError("Questions do not match the approved composer phase")
    return {"model": config.MAKERS_JEV_MODEL, "state": state, "questions": native}


def parse_response(result: dict, request: dict) -> dict[str, Any]:
    answers = result.get("answers", {})
    if set(answers) != set(request["questions"]):
        raise ValueError("Unexpected answers")
    checked = {}
    for key, question in request["questions"].items():
        a = answers[key]
        confidence = a.get("confidence")
        if (
            a.get("type") != "choice"
            or a.get("choice") not in question["criteria"]
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("Invalid answer")
        checked[key] = {"choice": a["choice"], "confidence": confidence}
    usage = result.get("usage", {})
    if any(
        type(usage.get(k)) is not int or usage[k] < 0 for k in ["input_tokens", "output_tokens"]
    ):
        raise ValueError("Invalid token usage")
    if not isinstance(result.get("model"), str) or not result["model"].startswith("jev-"):
        raise ValueError("Invalid model")
    return {
        "answers": checked,
        "model": result["model"],
        "usage": {k: usage[k] for k in ["input_tokens", "output_tokens"]},
    }


@router.post("/presentation-v4/evaluate")
async def evaluate(body: EvaluationRequest) -> dict[str, Any]:
    try:
        request = build_request(body)
        evidence.record("presentation_model_input", {"input": request})
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(400, "Invalid V4 composition request") from error
    if not config.MAKERS_MODELS_KEY:
        raise HTTPException(503, "Live presentation unavailable")
    started = time.perf_counter()
    try:

        async def send(client: httpx.AsyncClient) -> httpx.Response:
            return await client.post(
                config.MAKERS_JEV_ENDPOINT,
                headers={"Authorization": f"Bearer {config.MAKERS_MODELS_KEY}"},
                json=request,
            )

        if _client is not None:
            response = await send(_client)
        else:
            async with httpx.AsyncClient(timeout=config.MAKERS_JEV_TIMEOUT_SECONDS) as client:
                response = await send(client)
        evidence.record(
            "presentation_model_output",
            {"http_status": response.status_code, "output": response.json()},
        )
        response.raise_for_status()
        result = parse_response(response.json(), request)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(502, "Live presentation unavailable") from error
    return {**result, "latency_ms": round((time.perf_counter() - started) * 1000)}


ENQUIRY_COMPONENTS = {
    "balances": ["BalanceSummary", "AccountList"],
    "bills": ["UpcomingBills", "CashflowSummary"],
    "transactions": ["TransactionList"],
    "spending": ["SpendingBreakdown", "PeriodComparison"],
    "overview": ["BalanceSummary", "UpcomingBills", "SpendingBreakdown", "PeriodComparison"],
}


@router.post("/presentation-v5/enquiry")
async def enquiry(body: EnquiryPresentationRequest) -> dict[str, Any]:
    """Compose a bounded read-only tree. No user values enter the model request."""
    data = get_account_insights(body.user_id)
    required = list(ENQUIRY_COMPONENTS[body.topic])
    optional = {
        "balances": ["UpcomingBills"],
        "bills": ["AccountList"],
        "transactions": ["BalanceSummary"],
        "spending": ["TransactionList"],
        "overview": ["AccountList", "TransactionList"],
    }[body.topic]
    questions = {
        "layout": {
            "type": "choice",
            "instructions": "Choose a readable enquiry layout.",
            "criteria": {"focus": "One column", "split": "Two columns on wide screens"},
        },
        "detail": {
            "type": "choice",
            "instructions": "Choose how much supporting detail to show.",
            "criteria": {
                "compact": "Summary with expandable detail",
                "expanded": "Supporting detail visible",
            },
        },
    }
    for component in optional:
        questions[f"include_{component}"] = {
            "type": "choice",
            "instructions": f"Include {component} only if it helps explain this enquiry. "
            "Avoid clutter and redundant information.",
            "criteria": {"include": f"Show {component}", "omit": "Leave out optional context"},
        }
    diagrams = {"none": "No diagram; use readable text."}
    has_bills = any(f.get("commitments") for f in data["forecasts"])
    has_forecast = any(not f.get("unavailable") for f in data["forecasts"])
    if "UpcomingBills" in required + optional and has_bills:
        diagrams["timeline"] = "Show upcoming bills chronologically on a timeline."
    if "CashflowSummary" in required and has_forecast:
        diagrams["waterfall"] = (
            "Show balance plus scheduled income minus bills as a cashflow waterfall."
        )
    if len(diagrams) > 1:
        questions["diagram"] = {
            "type": "choice",
            "instructions": "Choose at most one useful diagram.",
            "criteria": diagrams,
        }
    diagram = "none"
    source, layout, detail = "fallback", "focus", "compact"
    started = time.perf_counter()
    if config.MAKERS_MODELS_KEY and _client is not None:
        try:
            request = {
                "model": config.MAKERS_JEV_MODEL,
                "state": {
                    "topic": body.topic,
                    "components": required,
                    "history_available": data["transactions"] is not None,
                    "schedule_available": all(not f.get("unavailable") for f in data["forecasts"]),
                },
                "questions": questions,
            }
            evidence.record("presentation_model_input", {"input": request})
            response = await _client.post(
                config.MAKERS_JEV_ENDPOINT,
                headers={"Authorization": f"Bearer {config.MAKERS_MODELS_KEY}"},
                json=request,
            )
            evidence.record(
                "presentation_model_output",
                {"http_status": response.status_code, "output": response.json()},
            )
            response.raise_for_status()
            checked = parse_response(response.json(), request)
            layout, detail = (checked["answers"][k]["choice"] for k in ["layout", "detail"])
            required.extend(
                c for c in optional if checked["answers"][f"include_{c}"]["choice"] == "include"
            )
            diagram = checked["answers"].get("diagram", {}).get("choice", "none")
            source = "jev"
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass  # A failed presentation call never removes the requested bank information.
    elements = {
        "workspace": {
            "type": "EnquiryWorkspace",
            "variant": layout,
            "children": [f"card{i}" for i in range(len(required))],
        }
    }
    for i, component in enumerate(required):
        variant = detail
        if component == "UpcomingBills" and diagram == "timeline":
            variant = "timeline"
        elif component == "CashflowSummary" and diagram == "waterfall":
            variant = "waterfall"
        elements[f"card{i}"] = {"type": component, "variant": variant, "children": []}
    return {
        "data": data,
        "spec": {"root": "workspace", "elements": elements},
        "source": source,
        "topic": body.topic,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


@router.get("/account-insights")
async def account_insights(user_id: str) -> dict[str, Any]:
    """Read synthetic demo insights; same user ownership rules as /accounts."""
    return get_account_insights(user_id)


class AnswerPresentationRequest(BaseModel):
    """Structural answer metadata, never account values or prompt text."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "balance", "transactions", "bills", "holdings", "quote", "fx_rate", "payees", "help"
    ]
    count: int = Field(ge=0, le=100000)
    timeline_requested: bool = False


@router.post("/presentation-v5/answer")
async def answer_presentation(body: AnswerPresentationRequest) -> dict[str, Any]:
    """Choose presentation without fetching or broadening Winston's answer."""
    timeline = body.kind == "bills" and body.count > 0
    variants = {"list": "Readable list with all records available"}
    if body.count > 1 and body.kind in {"balance", "holdings", "quote", "payees"}:
        variants["cards"] = "Separate cards, adapting columns to available width"
    if timeline:
        variants["timeline"] = "Bills arranged chronologically with recurrence labels"
    if timeline and body.timeline_requested:
        variants = {"timeline": variants["timeline"]}
    questions = {
        "variant": {
            "type": "choice",
            "instructions": "Choose the clearest presentation of this answer.",
            "criteria": variants,
        },
        "detail": {
            "type": "choice",
            "instructions": "Choose initial detail; remaining records stay expandable.",
            "criteria": {"compact": "First five records", "expanded": "All records visible"},
        },
    }
    summary_allowed = body.kind in {"balance", "holdings"} and body.count > 1
    if summary_allowed:
        questions["summary"] = {
            "type": "choice",
            "instructions": "Would per-currency totals help? Never combine currencies.",
            "criteria": {"include": "Totals above the records", "omit": "Records alone"},
        }
    result = {
        "source": "fallback",
        "variant": "timeline" if timeline and body.timeline_requested else "list",
        "detail": "compact",
        "summary": False,
    }
    started = time.perf_counter()
    if config.MAKERS_MODELS_KEY and _client is not None:
        try:
            request = {
                "model": config.MAKERS_JEV_MODEL,
                "state": body.model_dump(),
                "questions": questions,
            }
            evidence.record("presentation_model_input", {"input": request})
            response = await _client.post(
                config.MAKERS_JEV_ENDPOINT,
                headers={"Authorization": f"Bearer {config.MAKERS_MODELS_KEY}"},
                json=request,
            )
            evidence.record(
                "presentation_model_output",
                {"http_status": response.status_code, "output": response.json()},
            )
            response.raise_for_status()
            answers = parse_response(response.json(), request)["answers"]
            result.update(
                source="jev",
                variant=answers["variant"]["choice"],
                detail=answers["detail"]["choice"],
                summary=summary_allowed and answers["summary"]["choice"] == "include",
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass
    return {**result, "latency_ms": round((time.perf_counter() - started) * 1000)}
