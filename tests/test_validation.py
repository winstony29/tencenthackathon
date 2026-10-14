"""Semantic audit and stale-approval regression tests."""

import asyncio

import pytest

from app import transaction_state as state
from app.contracts import Draft
from app.validation import validator


@pytest.fixture
def draft():
    return Draft.model_validate(
        {
            "draft_id": "d_a",
            "user_id": "u_demo",
            "session_id": "s_a",
            "transcript": "send 50 to John",
            "created_at": "2026-10-14T09:00:00+08:00",
            "evidence": [],
            "actions": [
                {
                    "type": "transfer",
                    "from_account_id": "acc_1234",
                    "from_account_label": "Savings",
                    "to_payee_id": "pay_4521",
                    "to_payee_label": "John",
                    "amount": "50.00",
                    "currency": "SGD",
                }
            ],
        }
    )


def test_unknown_draft_cannot_be_validated(draft):
    state.clear()
    with pytest.raises(state.InvalidDraft):
        state.begin(draft)


def test_receipt_bound_to_current_exact_draft(draft):
    state.clear()
    state.register(draft, [{"text": draft.transcript}], {})
    record, cached = state.begin(draft)
    assert cached is None
    result = state.finish(record, {"status": "match"})
    state.authorize(draft, result["receipt"])
    changed = draft.model_copy(deep=True)
    changed.actions[0].amount += 1
    with pytest.raises(state.InvalidDraft):
        state.authorize(changed, result["receipt"])
    state.revoke(draft.user_id, draft.session_id)
    with pytest.raises(state.InvalidDraft):
        state.authorize(draft, result["receipt"])


def test_late_match_cannot_restore_superseded_draft(draft):
    state.clear()
    state.register(draft, [], {})
    record, _ = state.begin(draft)
    state.revoke(draft.user_id, draft.session_id)
    assert state.finish(record, {"status": "match"})["status"] == "stale"


def test_validator_unconfigured_never_matches(draft, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "VALIDATOR_PROVIDER", "compatible-api")
    monkeypatch.setattr(config, "VALIDATOR_MODEL", "")
    state.clear()
    state.register(draft, [{"text": draft.transcript}], {})
    assert asyncio.run(validator.validate(draft))["status"] == "unavailable"


def report(draft, verdict="match", field="actions[0].amount"):
    return {
        "checks": [
            {
                "field": name,
                "verdict": verdict if name == field else "match",
                "expected": "50.00",
                "note": "Checked independently",
            }
            for name in validator.fields(draft)
        ]
    }


@pytest.mark.parametrize("verdict", ["match", "mismatch", "uncertain"])
def test_audit_verdict_controls_receipt(draft, monkeypatch, verdict):
    async def audit(system, payload):
        assert "evidence" not in payload
        assert payload["history"] == [{"text": "send 50 to John"}]
        return report(draft, verdict)

    monkeypatch.setattr(validator.llm, "audit", audit)
    monkeypatch.setattr(validator, "append_event", lambda *a: None)
    state.clear()
    state.register(draft, [{"text": draft.transcript}], {})
    result = asyncio.run(validator.validate(draft))
    assert result["status"] == verdict
    assert ("receipt" in result) == (verdict == "match")
    if verdict != "match":
        with pytest.raises(state.InvalidDraft):
            state.authorize(draft, "forged")


def test_incomplete_model_response_fails_closed(draft, monkeypatch):
    async def audit(*args):
        return {"checks": []}

    monkeypatch.setattr(validator.llm, "audit", audit)
    state.clear()
    state.register(draft, [], {})
    assert asyncio.run(validator.validate(draft))["status"] == "unavailable"


def test_parallel_calls_run_one_model_and_late_result_is_stale(draft, monkeypatch):
    calls = []

    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        async def audit(*args):
            calls.append(1)
            started.set()
            await finish.wait()
            return report(draft)

        monkeypatch.setattr(validator.llm, "audit", audit)
        monkeypatch.setattr(validator, "append_event", lambda *a: None)
        state.clear()
        state.register(draft, [], {})
        first = asyncio.create_task(validator.validate(draft))
        await started.wait()
        assert (await validator.validate(draft))["status"] == "pending"
        state.revoke(draft.user_id, draft.session_id)
        finish.set()
        assert (await first)["status"] == "stale"

    asyncio.run(run())
    assert calls == [1]


def test_tampered_transcript_and_old_cancel_cannot_replace_provenance(draft):
    state.clear()
    state.register(draft, [{"text": draft.transcript}], {})
    changed = draft.model_copy(update={"transcript": "send 5000 to someone else"})
    with pytest.raises(state.InvalidDraft):
        state.begin(changed)
    state.register(changed, [{"text": "new explicit instruction"}], {})
    state.cancel(draft)
    assert state.begin(changed)[0].history == [{"text": "new explicit instruction"}]


def test_gateway_requires_match_and_never_returns_fake_execution(draft):
    from fastapi import HTTPException

    from app.bank import gateway

    state.clear()
    state.register(draft, [], {})
    with pytest.raises(HTTPException):
        gateway.challenge(draft, "fake")
    record, _ = state.begin(draft)
    receipt = state.finish(record, {"status": "match"})["receipt"]
    challenge = gateway.challenge(draft, receipt)
    with pytest.raises(HTTPException) as error:
        gateway.execute(challenge["nonce"])
    assert error.value.status_code == 503
    state.revoke(draft.user_id, draft.session_id)
    with pytest.raises(HTTPException) as error:
        gateway.execute(challenge["nonce"])
    assert error.value.status_code == 409


def test_expired_receipt_is_rejected(draft):
    state.clear()
    state.register(draft, [], {})
    record, _ = state.begin(draft)
    receipt = state.finish(record, {"status": "match"})["receipt"]
    record.expires = 0
    with pytest.raises(state.InvalidDraft):
        state.authorize(draft, receipt)


def test_older_intent_turn_cannot_publish_over_newer(draft):
    from contextvars import copy_context

    state.start_turn(draft.user_id, draft.session_id)
    older = copy_context()
    state.start_turn(draft.user_id, draft.session_id)
    state.register(draft, [], {})
    with pytest.raises(state.InvalidDraft):
        older.run(state.register, draft, [], {})


def test_fx_arithmetic_rejects_wrong_conversion_even_if_model_matches(draft, monkeypatch):
    from app.contracts import FxConversionAction

    draft.actions = [FxConversionAction(
        type="fx_conversion", from_account_id="acc_1234", from_account_label="Savings",
        to_account_id="acc_usd", to_account_label="USD", sell_amount="1000.00",
        sell_currency="SGD", buy_amount="1000.00", buy_currency="USD",
        rate="0.74", rate_as_of="2026-10-14T09:00:00+08:00",
    )]
    async def audit(*args):
        return report(draft)
    monkeypatch.setattr(validator.llm, "audit", audit)
    monkeypatch.setattr(validator, "append_event", lambda *a: None)
    state.register(draft, [], {"fx_rates":[{"from":"SGD", "to":"USD", "rate":"0.74"}]})
    result = asyncio.run(validator.validate(draft))
    assert result["status"] == "mismatch"
    assert result["fields"][0]["transcript_says"] == "740.00"
    assert "receipt" not in result
