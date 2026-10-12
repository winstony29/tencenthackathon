"""Mock bank tests: gateway, rules, ledger and devices.

Owner: Jayanth
Diagram box: Mock gateway, Risk & KYC rules, Mock ledger
Contract: docs/CONTRACTS.md §3.3 and §4
"""

import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.bank import readonly
from app.contracts import AccountsResponse, QuoteResponse
from app.main import app


@pytest.fixture
def bank_seed() -> dict[str, Any]:
    """Load the committed seed independently of the process working directory."""
    path = Path(__file__).resolve().parents[1] / "data" / "bank.json"

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            assert key not in result, f"Duplicate JSON key: {key}"
            result[key] = value
        return result

    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def test_seed_has_required_demo_records(bank_seed: dict[str, Any]) -> None:
    user = bank_seed["users"]["u_demo"]
    assert bank_seed["schema_version"] == "1.0"
    assert bank_seed["synthetic"] is True
    assert user["user_id"] == "u_demo"
    assert user["kyc"]["status"] == "complete"
    assert {(a["type"], a["currency"]) for a in user["accounts"]} == {
        ("savings", "SGD"),
        ("current", "SGD"),
        ("investment", "USD"),
    }
    assert {p["name"] for p in user["payees"]} == {"John Doe", "John Smith", "Tan Mei Ling", "Mum"}
    assert len(user["accounts"]) == 3
    assert len(user["payees"]) == 4
    assert len(user["billers"]) == 1
    assert user["billers"][0]["bill_reference"] == "4120-XXXX-XXXX-9012"
    assert len(bank_seed["quotes"]) == 5
    assert "AAPL" in bank_seed["quotes"]
    assert 10 <= len(user["transaction_history"]) <= 15
    assert all(a["status"] == "active" for a in user["accounts"])


def test_seed_preserves_contract_fields_and_demo_funding(bank_seed: dict[str, Any]) -> None:
    user = bank_seed["users"]["u_demo"]
    fields = {
        "accounts": ("account_id", "label", "type", "currency", "balance"),
        "payees": ("payee_id", "name", "label"),
        "billers": ("biller_id", "name", "label", "amount_due"),
        "holdings": ("ticker", "quantity"),
    }
    response = AccountsResponse.model_validate(
        {
            group: [{key: record[key] for key in keys} for record in user[group]]
            for group, keys in fields.items()
        }
    )
    accounts = {a.account_id: a for a in response.accounts}
    assert accounts["acc_1234"].label == "Savings …1234"
    assert accounts["acc_1234"].balance == Decimal("3200.00")
    assert accounts["acc_7788"].label == "Invest (USD) …7788"
    assert response.billers[0].biller_id == "bil_0001"
    assert response.billers[0].label == "DBS Credit Card …9012"
    assert response.billers[0].amount_due == Decimal("1240.00")
    assert any(h.ticker == "AAPL" and h.quantity == 10 for h in response.holdings)
    for ticker, quote in bank_seed["quotes"].items():
        public = QuoteResponse.model_validate(
            {key: quote[key] for key in ("ticker", "company_name", "price", "currency", "as_of")}
        )
        assert public.ticker == ticker
        assert public.currency == accounts["acc_7788"].currency
    apple = bank_seed["quotes"]["AAPL"]
    assert apple["price"] == "227.50"
    assert accounts["acc_7788"].balance >= Decimal(apple["price"]) * 100


def test_seed_ids_and_references_are_consistent(bank_seed: dict[str, Any]) -> None:
    user = bank_seed["users"]["u_demo"]
    identifiers = [user["user_id"]]
    for group, key, prefix in (
        ("accounts", "account_id", "acc_"),
        ("payees", "payee_id", "pay_"),
        ("billers", "biller_id", "bil_"),
        ("transaction_history", "transaction_id", "tx_"),
    ):
        for record in user[group]:
            assert record[key].startswith(prefix)
            identifiers.append(record[key])
    assert len(identifiers) == len(set(identifiers))
    accounts = {a["account_id"]: a for a in user["accounts"]}
    payees = {p["payee_id"] for p in user["payees"]}
    billers = {b["biller_id"] for b in user["billers"]}
    history = user["transaction_history"]
    assert len({t["reference"] for t in history}) == len(history)
    for transaction in history:
        assert transaction["currency"] == accounts[transaction["account_id"]]["currency"]
        assert transaction["direction"] in {"debit", "credit"}
        assert Decimal(transaction["amount"]) > 0
        if "payee_id" in transaction:
            assert transaction["payee_id"] in payees
        if "biller_id" in transaction:
            assert transaction["biller_id"] in billers
        if "ticker" in transaction:
            assert transaction["ticker"] in bank_seed["quotes"]
    for holding in user["holdings"]:
        quote = bank_seed["quotes"][holding["ticker"]]
        assert accounts[holding["account_id"]]["currency"] == quote["currency"]
        assert type(holding["quantity"]) is int
        assert holding["quantity"] >= 0


def test_seed_money_and_timestamps_are_valid(bank_seed: dict[str, Any]) -> None:
    def check(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"balance", "amount", "amount_due", "price"}:
                    assert isinstance(item, str)
                    assert re.fullmatch(r"\d+\.\d{2}", item)
                    assert Decimal(item).is_finite()
                if key in {"time", "as_of", "due_at"}:
                    assert datetime.fromisoformat(item).utcoffset() is not None
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(bank_seed)


def test_john_alias_remains_ambiguous(bank_seed: dict[str, Any]) -> None:
    payees = bank_seed["users"]["u_demo"]["payees"]
    matches = {
        p["payee_id"]: p["label"]
        for p in payees
        if "john" in {alias.casefold() for alias in p["aliases"]}
    }
    assert matches == {
        "pay_4521": "John Doe …4521",
        "pay_8892": "John Smith …8892",
    }


@pytest.fixture
def bank_client() -> TestClient:
    """Use the real app error handlers without calling external services."""
    return TestClient(app)


def test_accounts_endpoint_returns_only_public_seed_fields(
    bank_client: TestClient, bank_seed: dict[str, Any]
) -> None:
    response = bank_client.get("/api/accounts", params={"user_id": "u_demo"})
    assert response.status_code == 200
    result = response.json()
    user = bank_seed["users"]["u_demo"]
    fields = {
        "accounts": {"account_id", "label", "type", "currency", "balance"},
        "payees": {"payee_id", "name", "label"},
        "billers": {"biller_id", "name", "label", "amount_due"},
        "holdings": {"ticker", "quantity"},
    }
    assert set(result) == set(fields)
    for group, keys in fields.items():
        assert result[group] == [{key: record[key] for key in keys} for record in user[group]]
        assert all(set(record) == keys for record in result[group])
    assert isinstance(readonly.get_accounts("u_demo").accounts[0].balance, Decimal)


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"])
def test_quote_endpoint_returns_requested_seed_quote(
    bank_client: TestClient, bank_seed: dict[str, Any], ticker: str
) -> None:
    response = bank_client.get("/api/quote", params={"ticker": ticker})
    assert response.status_code == 200
    fields = {"ticker", "company_name", "price", "currency", "as_of"}
    assert response.json() == {key: bank_seed["quotes"][ticker][key] for key in fields}
    assert re.fullmatch(r"\d+\.\d{2}", response.json()["price"])
    assert isinstance(readonly.get_quote(ticker).price, Decimal)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("/api/accounts?user_id=u_missing", "User not found."),
        ("/api/quote?ticker=UNKNOWN", "Quote not found."),
    ],
)
def test_missing_bank_records_return_not_found(
    bank_client: TestClient, url: str, message: str
) -> None:
    response = bank_client.get(url)
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": message}}


@pytest.mark.parametrize("url", ["/api/accounts", "/api/quote"])
def test_bank_endpoints_require_query_parameters(bank_client: TestClient, url: str) -> None:
    response = bank_client.get(url)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    assert response.json()["error"]["message"]


def test_bank_reads_follow_seed_changes_without_writing(
    bank_client: TestClient,
    bank_seed: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_path = tmp_path / "app" / "bank" / "readonly.py"
    monkeypatch.setattr(readonly, "__file__", str(module_path))
    seed_path = tmp_path / "data" / "bank.json"
    seed_path.parent.mkdir()
    bank_seed["users"]["u_demo"]["accounts"][0]["balance"] = "9876.54"
    bank_seed["quotes"]["AAPL"]["price"] = "123.45"
    seed_path.write_text(json.dumps(bank_seed))
    before = seed_path.read_bytes()
    accounts = bank_client.get("/api/accounts?user_id=u_demo").json()
    assert accounts["accounts"][0]["balance"] == "9876.54"
    assert bank_client.get("/api/quote?ticker=AAPL").json()["price"] == "123.45"
    accounts["accounts"][0]["balance"] = "0.00"
    assert readonly.get_accounts("u_demo").accounts[0].balance == Decimal("9876.54")
    bank_seed["quotes"]["AAPL"]["price"] = "234.56"
    assert seed_path.read_bytes() == before
    seed_path.write_text(json.dumps(bank_seed))
    assert readonly.get_quote("AAPL").price == Decimal("234.56")


def test_bank_reads_ignore_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert readonly.get_accounts("u_demo").accounts[0].account_id == "acc_1234"
    assert readonly.get_quote("AAPL").ticker == "AAPL"


@pytest.mark.parametrize("content", [None, "not JSON", "{}", '{"schema_version":"1.0"}'])
def test_unavailable_or_invalid_database_never_returns_stub_data(
    content: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readonly, "__file__", str(tmp_path / "app" / "bank" / "readonly.py"))
    if content is not None:
        path = tmp_path / "data" / "bank.json"
        path.parent.mkdir()
        path.write_text(content)
    for read in (lambda: readonly.get_accounts("u_demo"), lambda: readonly.get_quote("AAPL")):
        with pytest.raises((OSError, ValueError)):
            read()


@pytest.mark.parametrize("field", ["balance", "price"])
def test_invalid_financial_seed_values_fail_validation(
    field: str,
    bank_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readonly, "_load_database", lambda: bank_seed)
    if field == "balance":
        bank_seed["users"]["u_demo"]["accounts"][0][field] = "invalid"
    else:
        bank_seed["quotes"]["AAPL"][field] = "invalid"
    with pytest.raises(ValidationError):
        if field == "balance":
            readonly.get_accounts("u_demo")
        else:
            readonly.get_quote("AAPL")
