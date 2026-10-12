"""Intent engine tests.

Owner: Winston
Diagram box: Intent detection column
Contract: docs/CONTRACTS.md §2 and §3.1
"""

import asyncio
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app import config
from app.intent import gate, jev, options
from tests.conftest import BANK, QUOTES

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


# --- numbers and options (W-02) ---


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("send fifty to john", ["50"]),
        ("send a grand to mum", ["1000"]),
        ("send 50k to mei ling", ["50000"]),
        ("send fifty thousand to mei ling", ["50000"]),
        ("transfer one hundred and fifty to john", ["150"]),
        ("pay $1,250.50 now", ["1250.50"]),
        ("send 5 grand", ["5000"]),
        ("send twenty five to john and 30 to mum", ["25", "30"]),
        ("buy 100 shares of apple", ["100"]),
        ("send a friend some money", []),
    ],
)
def test_numbers_are_parsed_by_code(text: str, values: list[str]) -> None:
    assert [str(n.value) for n in options.parse_numbers(text)] == values


def test_options_come_only_from_user_data_and_the_transcript() -> None:
    built = options.build("send fifty to john", options.UserData(BANK, QUOTES))
    criteria = {q.name: q.criteria for q in built.questions}
    assert set(criteria["recipient"]) == {"pay_4521", "pay_8892", "pay_3310", "pay_0001", "none"}
    assert set(criteria["transfer_amount"]) == {"n0", "none"}
    assert built.numbers == {"n0": Decimal("50")}
    assert set(criteria["ticker"]) == {"AAPL", "TSLA", "none"}


def test_options_for_buy_100_apple() -> None:
    built = options.build("buy 100 Apple", options.UserData(BANK, QUOTES))
    criteria = {q.name: q.criteria for q in built.questions}
    assert "AAPL" in criteria["ticker"]
    assert set(criteria["quantity"]) == {"n0", "none"} and built.numbers["n0"] == Decimal("100")
    assert set(criteria["side"]) == {"buy", "sell"}


# --- gate (W-05) ---


def _replies(*tables: dict[str, float]) -> list[jev.Reply]:
    return [
        jev.Reply("jev-test", {"q": jev.Answer(max(t, key=t.get), max(t.values()), t)})
        for t in tables
    ]


def test_gate_proceeds_when_sure_and_consistent() -> None:
    p = gate.pick(_replies({"a": 0.95, "b": 0.05}, {"a": 0.9, "b": 0.1}), "q")
    assert (p.option, round(p.spread, 2)) == ("a", 0.05)
    assert gate.trusted(p, Decimal("50"))


def test_gate_asks_when_average_is_low() -> None:
    assert not gate.trusted(gate.pick(_replies({"a": 0.6, "b": 0.4}), "q"), Decimal("50"))


def test_gate_asks_when_rewrites_disagree() -> None:
    p = gate.pick(_replies({"a": 0.99, "b": 0.01}, {"a": 0.7, "b": 0.3}), "q")
    assert p.average_probability > 0.8 and not gate.trusted(p, Decimal("50"))


def test_gate_tau_rises_with_the_amount() -> None:
    p = gate.pick(_replies({"a": 0.85, "b": 0.15}), "q")
    assert gate.trusted(p, Decimal("50")) and not gate.trusted(p, Decimal("50000"))


# --- /api/understand end to end (W-06, W-07) ---

SURE = 0.97


def _understand(client, transcript: str | None = None, answer: tuple[str, str] | None = None):
    body = {"user_id": "u_demo", "session_id": "s_test"}
    if transcript is not None:
        body["transcript"] = transcript
    else:
        body["answer"] = {"question_id": answer[0], "option_id": answer[1]}
    return client.post("/api/understand", json=body)


def _send_fifty_to_john(fake_jev) -> None:
    fake_jev.script = {
        "has_transfer": {"yes": SURE, "no": 1 - SURE},
        "recipient": {"pay_4521": 0.5, "pay_8892": 0.5},
        "transfer_amount": {"n0": SURE, "none": 1 - SURE},
    }


def test_two_johns_ask_then_answer_builds_the_draft(client, fake_jev, bank) -> None:
    _send_fifty_to_john(fake_jev)
    reply = _understand(client, "send fifty to john").json()
    assert reply["status"] == "question"
    assert reply["question"]["text"] == "Which John did you mean?"
    assert {o["option_id"] for o in reply["question"]["options"]} == {"pay_4521", "pay_8892"}
    assert reply["meta"]["source"] == "jev" and reply["meta"]["cached"] is False

    reply = _understand(client, answer=(reply["question"]["question_id"], "pay_4521")).json()
    assert reply["status"] == "draft", reply
    action = reply["draft"]["actions"][0]
    assert action == {
        "type": "transfer",
        "from_account_id": "acc_1234",
        "from_account_label": "Savings …1234",
        "to_payee_id": "pay_4521",
        "to_payee_label": "John Doe …4521",
        "amount": "50.00",
        "currency": "SGD",
    }
    assert len(fake_jev.calls) == 1  # the answer reused the stored replies
    assert reply["meta"]["cached"] is True


def test_none_of_these_cancels(client, fake_jev, bank) -> None:
    _send_fifty_to_john(fake_jev)
    question = _understand(client, "send fifty to john").json()["question"]
    reply = _understand(client, answer=(question["question_id"], "none")).json()
    assert (reply["status"], reply["reason"]) == ("cancelled", "user_declined")
    assert _understand(client, answer=(question["question_id"], "pay_4521")).status_code == 404


def test_an_option_that_was_not_offered_is_refused(client, fake_jev, bank) -> None:
    _send_fifty_to_john(fake_jev)
    question = _understand(client, "send fifty to john").json()["question"]
    assert _understand(client, answer=(question["question_id"], "pay_3310")).status_code == 400


def test_bill_and_shares_make_two_actions_in_spoken_order(client, fake_jev, bank) -> None:
    fake_jev.script = {
        "has_bill_payment": {"yes": SURE, "no": 1 - SURE},
        "has_stock_order": {"yes": SURE, "no": 1 - SURE},
        "biller": {"bil_0001": SURE, "none": 1 - SURE},
        "bill_amount": {"due": SURE, "n0": 1 - SURE},
        "ticker": {"AAPL": SURE, "none": 1 - SURE},
        "side": {"buy": SURE, "sell": 1 - SURE},
        "quantity": {"n0": SURE, "none": 1 - SURE},
    }
    reply = _understand(client, "pay my credit card bill and buy 100 shares of Apple").json()
    assert reply["status"] == "draft", reply
    bill, stock = reply["draft"]["actions"]
    assert (bill["type"], bill["amount"], bill["bill_reference"]) == (
        "bill_payment",
        "1240.00",
        "4120-XXXX-XXXX-9012",
    )
    assert stock == {
        "type": "stock_order",
        "from_account_id": "acc_7788",
        "from_account_label": "Invest (USD) …7788",
        "side": "buy",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "quantity": 100,
        "order_type": "market",
        "limit_price": None,
        "currency": "USD",
    }


def test_large_amounts_need_more_certainty(client, fake_jev, bank) -> None:
    fake_jev.script = {
        "has_transfer": {"yes": SURE, "no": 1 - SURE},
        "recipient": {"pay_3310": 0.85, "pay_4521": 0.15},
        "transfer_amount": {"n0": SURE, "none": 1 - SURE},
    }
    reply = _understand(client, "send fifty thousand to mei ling").json()
    assert reply["status"] == "question"  # 0.85 passes for small amounts, not for 50,000

    fake_jev.script["recipient"] = {"pay_3310": SURE, "pay_4521": 1 - SURE}
    reply = _understand(client, "send fifty thousand to mei ling").json()
    assert reply["draft"]["actions"][0]["amount"] == "50000.00"


def test_a_missing_amount_is_never_guessed(client, fake_jev, bank) -> None:
    fake_jev.script = {
        "has_transfer": {"yes": SURE, "no": 1 - SURE},
        "recipient": {"pay_0001": SURE, "none": 1 - SURE},
    }
    reply = _understand(client, "send money to mum").json()
    assert reply["status"] == "unsupported"
    assert "how much" in reply["message"]


def test_not_a_banking_command_is_unsupported(client, fake_jev, bank) -> None:
    assert _understand(client, "what's the weather").json()["status"] == "unsupported"


def test_jev_failure_is_an_upstream_error(client, fake_jev, bank) -> None:
    fake_jev.error = HTTPException(502, "Jev is unavailable right now. Please try again.")
    response = _understand(client, "send fifty to john")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_stubs_answer_until_jev_and_bank_data_exist(client) -> None:
    assert _understand(client, "send fifty to john").json()["question"]["question_id"] == "q_1"
