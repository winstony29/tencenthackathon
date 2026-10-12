"""Offline tests for the presentation-only native protocol."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from web.presentation import ComponentContext, build_request, parse_response


def context(objective="prepare"):
    return ComponentContext(
        action_types=["transfer"],
        complete=True,
        missing=[],
        objective=objective,
        recipient_status="listed",
        available_context=["accounts", "recipients", "projection"],
    )


def response(payload):
    return {
        "model": "jev-test",
        "answers": {
            k: {
                "type": "choice",
                "choice": next(iter(q["criteria"])),
                "confidence": 1,
                "probabilities": {o: int(i == 0) for i, o in enumerate(q["criteria"])},
            }
            for k, q in payload["questions"].items()
        },
    }


@pytest.mark.parametrize(
    "objective", ["prepare", "assess_affordability", "simulate", "check_recipient", "review"]
)
def test_native_choices_are_checked(objective):
    payload = build_request(context(objective))
    assert parse_response(response(payload), payload)["source"] == "jev"
    assert not any(word in str(payload) for word in ["acc_1234", "500.00", "John Doe"])
    if objective == "review":
        assert list(payload["questions"]["primary"]["criteria"]) == ["transaction_review"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("choice", "execute_money"),
        ("confidence", float("nan")),
        ("confidence", True),
        ("probabilities", {"arbitrary": 1}),
    ],
)
def test_rejects_invalid_response(field, value):
    payload = build_request(context())
    bad = response(payload)
    bad["answers"]["primary"][field] = value
    with pytest.raises(ValueError):
        parse_response(bad, payload)


def test_incomplete_and_private_context_rejected_before_gateway():
    client = TestClient(app)
    for delta in [
        {"complete": False},
        {"missing": ["amount"]},
        {"amount": "500.00"},
        {
            "current_ui": {
                "primary": "John Doe",
                "components": [],
                "layout": "focus",
                "density": "compact",
            }
        },
    ]:
        body = context().model_dump() | delta
        assert client.post("/api/presentation-v3", json=body).status_code in (400, 422)


def test_inconsistent_data_rejected():
    body = deepcopy(context().model_dump())
    body["available_context"] = ["accounts"]
    with pytest.raises(ValidationError):
        ComponentContext(**body)
