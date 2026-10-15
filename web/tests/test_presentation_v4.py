"""V4 tests use synthetic replies; never call the gateway."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from web.presentation_v4 import Context, EvaluationRequest, build_request, parse_response, recipes


@pytest.fixture(autouse=True)
def isolated_presentation_evidence(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    from app import config

    monkeypatch.setattr(config, "AUDIT_EVIDENCE_PATH", str(tmp_path / "evidence.sqlite3"))
    monkeypatch.setattr(config, "AUDIT_EVIDENCE_KEY", Fernet.generate_key().decode())


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


def test_evaluations_reuse_lifespan_client(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from web import presentation_v4 as module

    request_body = body()
    native = build_request(request_body)
    reply = {
        "model": "jev-test",
        "answers": {
            k: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 1}
            for k, q in native["questions"].items()
        },
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    client = Mock()
    client.post = AsyncMock(return_value=Mock(status_code=200, json=lambda: reply))
    monkeypatch.setattr(module, "_client", client)
    monkeypatch.setattr(module.config, "MAKERS_MODELS_KEY", "test")

    async def run():
        await module.evaluate(request_body)
        await module.evaluate(request_body)

    asyncio.run(run())
    assert client.post.await_count == 2


def test_enquiry_fallback_retains_requested_information(monkeypatch):
    import asyncio

    from app.contracts import EnquiryPresentationRequest
    from web import presentation_v4 as module

    monkeypatch.setattr(module, "_client", None)
    result = asyncio.run(
        module.enquiry(EnquiryPresentationRequest(user_id="u_demo", topic="balances"))
    )
    assert result["source"] == "fallback"
    assert [e["type"] for e in result["spec"]["elements"].values()] == [
        "EnquiryWorkspace",
        "BalanceSummary",
        "AccountList",
    ]
    assert result["data"]["source"] == "synthetic"
    assert all("status" not in a for a in result["data"]["accounts"])
    assert "draft" not in result


def test_enquiry_diagram_selected_in_same_call(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from app.contracts import EnquiryPresentationRequest
    from web import presentation_v4 as module

    async def post(*args, **kwargs):
        request = kwargs["json"]
        answers = {
            k: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 1}
            for k, q in request["questions"].items()
        }
        answers["diagram"]["choice"] = "waterfall"
        return Mock(
            status_code=200,
            json=lambda: {
                "model": "jev-test",
                "answers": answers,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = Mock(post=AsyncMock(side_effect=post))
    monkeypatch.setattr(module, "_client", client)
    monkeypatch.setattr(module.config, "MAKERS_MODELS_KEY", "test")
    result = asyncio.run(
        module.enquiry(EnquiryPresentationRequest(user_id="u_demo", topic="bills"))
    )
    assert client.post.await_count == 1
    assert result["source"] == "jev"
    diagrams = [
        e for e in result["spec"]["elements"].values() if e["variant"] in {"timeline", "waterfall"}
    ]
    assert len(diagrams) == 1
    assert diagrams[0]["type"] == "CashflowSummary"


def test_transfer_flow_variant_not_offered_for_other_actions():
    for kind in ["transfer", "bill_payment", "stock_order", "fx_conversion"]:
        context = Context(
            objective="prepare", action_types=[kind], alternatives=False, viewport="desktop"
        )
        flows = [r for r in recipes(context) if r["id"].endswith("_flow")]
        assert bool(flows) == (kind == "transfer")


@pytest.mark.parametrize(
    "kind", ["balance", "transactions", "bills", "holdings", "quote", "fx_rate", "payees", "help"]
)
def test_answer_presentation_never_fetches_bank_data(monkeypatch, kind):
    import asyncio

    from web import presentation_v4 as module

    def forbidden(*args, **kwargs):
        raise AssertionError("Presentation must not replace Winston's data")

    monkeypatch.setattr(module, "get_account_insights", forbidden)
    monkeypatch.setattr(module, "_client", None)
    result = asyncio.run(
        module.answer_presentation(
            module.AnswerPresentationRequest(kind=kind, count=2, timeline_requested=True)
        )
    )
    assert result["source"] == "fallback"
    assert result["variant"] == ("timeline" if kind == "bills" else "list")
    assert "data" not in result


def test_answer_presentation_rejects_ineligible_model_choice(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from web import presentation_v4 as module

    async def post(*args, **kwargs):
        request = kwargs["json"]
        assert set(request["state"]) == {
            "kind",
            "count",
            "timeline_requested",
            "intent",
            "bank_capabilities",
        }
        assert "timeline" not in request["questions"]["variant"]["criteria"]
        answers = {
            k: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 1}
            for k, q in request["questions"].items()
        }
        answers["variant"]["choice"] = "timeline"
        return Mock(
            status_code=200,
            json=lambda: {
                "model": "jev-test",
                "answers": answers,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = Mock(post=AsyncMock(side_effect=post))
    monkeypatch.setattr(module, "_client", client)
    monkeypatch.setattr(module.config, "MAKERS_MODELS_KEY", "test")
    result = asyncio.run(
        module.answer_presentation(module.AnswerPresentationRequest(kind="transactions", count=2))
    )
    assert result["source"] == "fallback"
    assert client.post.await_count == 1


def _plan_body():
    from app.contracts import PlanPresentationRequest

    return PlanPresentationRequest.model_validate(
        {
            "plan": {
                "request": "If AAPL is below US$300, send John 50 dollars",
                "condition": {
                    "text": "AAPL is below US$300",
                    "ticker": "AAPL",
                    "operator": "lt",
                    "threshold": "300.00",
                    "currency": "USD",
                },
                "actions": [],
                "executable": False,
                "limitation": "Conditional payments unavailable",
                "intent": {
                    "request": "If AAPL is below US$870, send [recipient] 92 dollars",
                    "constraints": ["stock threshold"],
                },
            }
        }
    )


def test_plan_model_input_has_context_and_no_financial_values():
    import json

    from web.presentation_v4 import plan_request

    request = plan_request(_plan_body())
    payload = json.dumps(request)
    assert "300.00" not in payload and "John" not in payload
    assert "conditional_payment" in request["state"]["bank_capabilities"]
    assert request["state"]["bank_capabilities"]["conditional_payment"] in {
        "execution_disabled",
        "virtual_bank_check_at_preparation_then_confirm",
    }
    assert "intent" in request["state"]
    assert "quote" not in request["questions"]


def test_plan_composition_fallback_keeps_condition_relationship_and_notice(monkeypatch):
    import asyncio

    from web import presentation_v4 as module

    monkeypatch.setattr(module, "_client", None)
    result = asyncio.run(module.plan_presentation(_plan_body()))
    assert [n["component"] for n in result["nodes"]] == [
        "Condition",
        "Relationship",
        "AvailabilityNotice",
    ]
    assert result["source"] == "fallback"


def test_invalid_plan_model_choice_cannot_create_action_or_erase_condition(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from web import presentation_v4 as module

    native = module.plan_request(_plan_body())
    answers = {
        k: {"type": "choice", "choice": next(iter(v["criteria"])), "confidence": 1}
        for k, v in native["questions"].items()
    }
    answers["condition"]["choice"] = "omit"
    client = Mock(
        post=AsyncMock(
            return_value=Mock(
                status_code=200,
                json=lambda: {
                    "model": "test",
                    "answers": answers,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        )
    )
    monkeypatch.setattr(module, "_client", client)
    monkeypatch.setattr(module.config, "MAKERS_MODELS_KEY", "test")
    result = asyncio.run(module.plan_presentation(_plan_body()))
    assert result["source"] == "fallback"
    assert result["nodes"][0]["component"] == "Condition"
    assert client.post.call_count == 1


@pytest.mark.parametrize(
    "text",
    [
        "Send John Doe S$50 and show my upcoming bills",
        "Compare my spending months even if the task result only contains a transfer",
        "Only send John Doe S$50 when Apple stocks is less than US$300",
    ],
)
def test_request_composition_uses_full_context_and_all_available_components(monkeypatch, text):
    import asyncio
    import json
    from unittest.mock import AsyncMock, Mock

    from app.contracts import EnquiryPresentationRequest, PresentationIntent
    from web import presentation_v4 as module

    async def post(*args, **kwargs):
        native = kwargs["json"]
        context = native["state"]["request_context"]
        assert context["outcome"] == "draft"
        assert context["winston"]["tasks"] == ["transfer"]
        assert "John Doe" not in json.dumps(native)
        assert "context_include_UpcomingBills" in native["questions"]
        assert "context_include_PeriodComparison" in native["questions"]
        assert "context_quote_AAPL" in native["questions"]
        answers = {}
        for key, q in native["questions"].items():
            choice = next(iter(q["criteria"]))
            if key.startswith("context_include_") or key.startswith("context_quote_"):
                choice = "omit"
            if key == "context_include_UpcomingBills":
                choice = "include"
            if key == "context_quote_AAPL":
                choice = "include"
            answers[key] = {"type": "choice", "choice": choice, "confidence": 1}
        return Mock(
            status_code=200,
            json=lambda: {
                "model": "jev-test",
                "answers": answers,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = Mock(post=AsyncMock(side_effect=post))
    monkeypatch.setattr(module, "_client", client)
    monkeypatch.setattr(module.config, "MAKERS_MODELS_KEY", "test")
    request = body()
    request.context_request = EnquiryPresentationRequest(
        user_id="u_demo",
        topic="request",
        request=text,
        outcome="draft",
        winston=PresentationIntent(
            request="transfer", tasks=["transfer"], coverage="partial", gaps=["other"]
        ),
    )
    result = asyncio.run(module.evaluate(request))
    assert not any(k.startswith("context_") for k in result["answers"])
    value = result["context_view"]
    assert value["source"] == "jev"
    assert "coverage" not in value
    assert {n["type"] for n in value["spec"]["elements"].values()} == {
        "EnquiryWorkspace",
        "UpcomingBills",
        "QuoteList",
    }
    assert value["data"]["quotes"][0]["ticker"] == "AAPL"
    assert client.post.await_count == 1


def test_request_composition_failure_has_no_authorization_effect(monkeypatch):
    import asyncio

    from app.contracts import EnquiryPresentationRequest
    from web import presentation_v4 as module

    monkeypatch.setattr(module, "_client", None)
    value = asyncio.run(
        module.enquiry(
            EnquiryPresentationRequest(
                user_id="u_demo", topic="request", request="Do something Winston missed"
            )
        )
    )
    assert "coverage" not in value
    assert value["spec"]["elements"]["workspace"]["children"] == []


@pytest.mark.parametrize(
    "kind,covered",
    [
        ("bills", ["UpcomingBills"]),
        ("balance", ["BalanceSummary", "AccountList"]),
        ("transactions", ["TransactionList"]),
        ("quote", ["QuoteList"]),
    ],
)
def test_supplementary_context_cannot_duplicate_primary_answer(kind, covered):
    from app.contracts import EnquiryPresentationRequest
    from web import presentation_v4 as module

    native = {"state": {"kind": kind}, "questions": {}}
    prepared = module.add_context(
        native,
        EnquiryPresentationRequest(
            user_id="u_demo",
            topic="request",
            request="Show my records",
            outcome="answer",
            winston=module.PresentationIntent(
                request="Show my records", coverage="partial", gaps=["spending"]
            ),
        ),
    )
    assert set(covered).isdisjoint(prepared["optional"])
    if "QuoteList" in covered:
        assert prepared["quotes"] == []
    assert native["state"]["request_context"]["already_shown"] == covered


def test_context_card_budget_keeps_strongest_choices_and_discards_unshown_quotes():
    from app.contracts import EnquiryPresentationRequest
    from web import presentation_v4 as module

    prepared = module.prepare_enquiry(
        EnquiryPresentationRequest(
            user_id="u_demo", topic="request", request="Show spending and bills", outcome="draft"
        )
    )
    answers = {
        k: {"choice": next(iter(q["criteria"])), "confidence": 0.1}
        for k, q in prepared["request"]["questions"].items()
    }
    for component in ["SpendingBreakdown", "UpcomingBills"]:
        answers[f"include_{component}"] = {"choice": "include", "confidence": 0.9}
    result = module.render_enquiry(prepared, answers)
    assert [n["type"] for k, n in result["spec"]["elements"].items() if k != "workspace"] == [
        "SpendingBreakdown",
        "UpcomingBills",
    ]
    assert result["data"]["quotes"] == []


@pytest.mark.parametrize("coverage,gaps", [("complete", []), ("partial", [])])
def test_structured_composition_does_not_reinterpret_original_request(coverage, gaps):
    from app.contracts import EnquiryPresentationRequest, PresentationIntent
    from web import presentation_v4 as module

    intent = PresentationIntent(
        request="Private original wording", tasks=["transfer"], coverage=coverage, gaps=gaps
    )
    native = {"state": {}, "questions": {}}
    assert (
        module.add_context(
            native,
            EnquiryPresentationRequest(
                user_id="u_demo",
                topic="request",
                request=intent.request,
                outcome="draft",
                winston=intent,
            ),
        )
        is None
    )
    assert native == {"state": {}, "questions": {}}
    if coverage == "complete":
        assert module.model_intent(intent) == {"tasks": ["transfer"], "coverage": "complete"}
