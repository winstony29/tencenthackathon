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

from app.contracts import AccountsResponse, QuoteResponse


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
