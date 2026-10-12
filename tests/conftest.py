"""Shared test fixtures: test client and fake Jev, LLM and ASR.

Owner: Winston (shared)
Diagram box: none
Contract: none
"""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.bank import readonly
from app.contracts import AccountsResponse, QuoteResponse
from app.intent import jev
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class FakeJev:
    """Stands in for `jev.ask`: tests script the probabilities per question name.

    Unscripted questions answer their neutral option ("none", "no", "default",
    "market") with certainty, so a test only scripts what it is about.
    """

    def __init__(self) -> None:
        self.script: dict[str, dict[str, float]] = {}
        self.calls: list[tuple[str, list[jev.Question]]] = []
        self.error: Exception | None = None

    async def ask(self, text: str, questions: list[jev.Question]) -> jev.Reply:
        self.calls.append((text, questions))
        if self.error:
            raise self.error
        return jev.Reply("jev-fake", {q.name: self._answer(q) for q in questions})

    def _answer(self, question: jev.Question) -> jev.Answer:
        wanted = self.script.get(question.name) or {_neutral(question): 1.0}
        probabilities = {option: float(wanted.get(option, 0.0)) for option in question.criteria}
        choice = max(probabilities, key=probabilities.get)
        return jev.Answer(choice, probabilities[choice], probabilities)


def _neutral(question: jev.Question) -> str:
    for option in ("none", "no", "default", "market"):
        if option in question.criteria:
            return option
    return next(iter(question.criteria))


@pytest.fixture
def fake_jev(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    fake = FakeJev()
    monkeypatch.setattr(config, "MAKERS_MODELS_KEY", "test-key")
    monkeypatch.setattr(jev, "ask", fake.ask)
    return fake


BANK = AccountsResponse.model_validate(
    {
        "accounts": [
            {
                "account_id": "acc_1234",
                "label": "Savings …1234",
                "type": "savings",
                "currency": "SGD",
                "balance": "3200.00",
            },
            {
                "account_id": "acc_5678",
                "label": "Current …5678",
                "type": "current",
                "currency": "SGD",
                "balance": "900.00",
            },
            {
                "account_id": "acc_7788",
                "label": "Invest (USD) …7788",
                "type": "invest",
                "currency": "USD",
                "balance": "25000.00",
            },
        ],
        "payees": [
            {"payee_id": "pay_4521", "name": "John Doe", "label": "John Doe …4521"},
            {"payee_id": "pay_8892", "name": "John Smith", "label": "John Smith …8892"},
            {"payee_id": "pay_3310", "name": "Tan Mei Ling", "label": "Tan Mei Ling …3310"},
            {"payee_id": "pay_0001", "name": "Mum", "label": "Mum …0001"},
        ],
        "billers": [
            {
                "biller_id": "bil_0001",
                "name": "DBS Credit Card",
                "label": "DBS Credit Card …9012",
                "amount_due": "1240.00",
            }
        ],
        "holdings": [{"ticker": "AAPL", "quantity": 10}],
    }
)
QUOTES = [
    QuoteResponse(
        ticker=t, company_name=n, price=p, currency="USD", as_of="2026-10-12T09:30:00+08:00"
    )
    for t, n, p in [("AAPL", "Apple Inc.", "227.50"), ("TSLA", "Tesla Inc.", "251.00")]
]


@pytest.fixture
def bank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands in for Jayanth's app/bank/readonly.py (CONTRACTS §3.3) until J-02 lands."""
    monkeypatch.setattr(readonly, "get_accounts", lambda user_id: BANK, raising=False)
    monkeypatch.setattr(readonly, "list_quotes", lambda: QUOTES, raising=False)
    monkeypatch.setattr(
        readonly, "get_bill_reference", lambda biller_id: "4120-XXXX-XXXX-9012", raising=False
    )
