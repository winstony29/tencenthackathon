"""Intent engine tests.

Owner: Winston
Diagram box: Intent detection column
Contract: docs/CONTRACTS.md §2 and §3.1
"""

import asyncio

import pytest
from fastapi import HTTPException

from app import config
from app.intent import jev

RECIPIENT = jev.Question("recipient", "Who gets the money?", {"pay_4521": "John Doe", "none": "-"})


def _raw(choice: str = "pay_4521", **overrides: object) -> dict:
    answer = {
        "type": "choice",
        "choice": choice,
        "confidence": 0.9,
        "probabilities": {"pay_4521": 0.9, "none": 0.1},
    }
    return {"model": "jev-1.13.0", "answers": {"recipient": {**answer, **overrides}}}


def test_jev_parse_accepts_an_offered_choice() -> None:
    reply = jev.parse(_raw(), [RECIPIENT])
    assert reply.model == "jev-1.13.0"
    assert reply.answers["recipient"].choice == "pay_4521"
    assert reply.answers["recipient"].probabilities == {"pay_4521": 0.9, "none": 0.1}


@pytest.mark.parametrize(
    "bad",
    [
        _raw(choice="pay_9999"),
        _raw(probabilities={"pay_4521": 0.9}),
        _raw(probabilities={"pay_4521": 0.9, "none": 0.1, "pay_9999": 0.0}),
        _raw(confidence=1.5),
        _raw(confidence=True),
        {"answers": {}},
        {"model": "jev-1.13.0"},
    ],
)
def test_jev_parse_refuses_anything_not_offered(bad: dict) -> None:
    with pytest.raises(ValueError):
        jev.parse(bad, [RECIPIENT])


def test_jev_without_key_is_an_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAKERS_MODELS_KEY", "")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(jev.ask("send fifty to john", [RECIPIENT]))
    assert caught.value.status_code == 502


def test_jev_asks_every_phrasing_in_order(fake_jev) -> None:
    replies = asyncio.run(jev.ask_all(["send fifty to john", "transfer 50 to john"], [RECIPIENT]))
    assert [text for text, _ in fake_jev.calls] == ["send fifty to john", "transfer 50 to john"]
    assert len(replies) == 2
