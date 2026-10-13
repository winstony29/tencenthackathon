"""V4 tests use synthetic replies; never call the gateway."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from web.presentation_v4 import Context, EvaluationRequest, build_request, parse_response, recipes


def body():
    context = Context(
        objective="simulate", action_types=["transfer"], alternatives=False, viewport="desktop"
    )
    pool = recipes(context)
    groups = {}
    for r in pool:
        groups.setdefault(r["resource"], []).append(r)
    questions = {"root": [r["id"] for r in pool if r["component"] == "Workspace"] + ["unavailable"]}
    questions.update(
        {
            f"select_{i}": ["omit"] + [f"use:{r['id']}" for r in group]
            for i, group in enumerate(groups.values())
        }
    )
    return EvaluationRequest(context=context, questions=questions)


def test_required_choices_cannot_be_omitted_and_values_are_not_sent():
    request = build_request(body())
    for q in list(request["questions"].values())[2:]:
        assert "omit" not in q["criteria"]
    assert "amount" not in request["state"]["context"]
    assert "unavailable" not in request["questions"]["root"]["criteria"]


def test_client_cannot_inject_prose_or_choices():
    b = body()
    b.questions["root"].append("execute a payment")
    with pytest.raises(ValueError):
        build_request(b)
    raw = body().model_dump()
    raw["context"]["transcript"] = "send money"
    with pytest.raises(ValidationError):
        EvaluationRequest.model_validate(raw)


def test_checked_response_rejects_bad_choice_confidence_and_usage():
    request = build_request(body())
    response = {
        "model": "jev-test",
        "answers": {
            k: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 1}
            for k, q in request["questions"].items()
        },
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    assert parse_response(response, request)["model"] == "jev-test"
    for field, value in [("confidence", True), ("confidence", float("nan")), ("choice", "money")]:
        bad = deepcopy(response)
        bad["answers"]["root"][field] = value
        with pytest.raises(ValueError):
            parse_response(bad, request)
    response["usage"]["input_tokens"] = -1
    with pytest.raises(ValueError):
        parse_response(response, request)


def test_layout_limits_editors_to_their_task_and_outcome_to_decision_group():
    b = body()
    b.selected = [
        "Workspace_all_focus",
        "TaskGroup_0_default",
        "TaskEditor_0_full",
        "DecisionGroup_all_paired",
        "BalanceProjection_all_summary",
        "AmountSimulator_all_default",
    ]
    pool = {r["id"]: r for r in recipes(b.context)}
    selected = [pool[k] for k in b.selected]
    b.questions = {}
    for i in range(1, len(selected)):
        b.questions[f"parent_node_{i}"] = [
            f"node_{j}:{slot}" for j, r in enumerate(selected) if j != i for slot in r["slots"]
        ]
        b.questions[f"order_node_{i}"] = [str(j) for j in range(1, len(selected))]
    request = build_request(b)
    assert list(request["questions"]["parent_node_2"]["criteria"]) == ["node_1:details"]
    assert list(request["questions"]["parent_node_4"]["criteria"]) == ["node_3:outcome"]


def test_financial_context_gates_insights_and_rejects_raw_financial_values():
    context = Context(
        objective="assess_affordability",
        action_types=["transfer"],
        alternatives=False,
        viewport="desktop",
    )
    assert not any(r["component"] == "AccountInsights" for r in recipes(context))
    context.financial_context = True
    context.reserve = "breached"
    assert {r["id"] for r in recipes(context) if r["component"] == "AccountInsights"} == {
        "AccountInsights_all_compact",
        "AccountInsights_all_expanded",
    }
    raw = context.model_dump()
    raw["balance"] = "3200.00"
    with pytest.raises(ValidationError):
        Context.model_validate(raw)
