"""Mock bank tests: gateway, rules, ledger and devices.

Owner: Jayanth
Diagram box: Mock gateway, Risk & KYC rules, Mock ledger
Contract: docs/CONTRACTS.md §3.3 and §4
"""

import ast
import base64
import copy
import hashlib
import hmac
import importlib
import json
import re
import secrets
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import audit, config
from app.bank import devices, gateway, ledger, readonly, rules
from app.contracts import (
    Account,
    AccountsResponse,
    ChallengeRequest,
    Draft,
    ExecuteRequest,
    QuoteResponse,
)
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

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


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
    assert {p["name"] for p in user["payees"]} >= {"John Doe", "John Smith", "Tan Mei Ling", "Mum"}
    assert len(user["accounts"]) == 3
    assert len(user["payees"]) == 10
    assert len(user["billers"]) == 1
    assert user["billers"][0]["bill_reference"] == "4120-XXXX-XXXX-9012"
    assert len(bank_seed["quotes"]) == 8
    assert "AAPL" in bank_seed["quotes"]
    assert len(user["transaction_history"]) == 19
    assert all(a["status"] == "active" for a in user["accounts"])


@pytest.mark.parametrize(
    "section,expected",
    [
        ("accounts", "208070941c972867c3dbfb96920bcf79423cc87b4eb3cf5cc52ea2b065c4497e"),
        ("payees", "4f6f52554496a4e02eef1bac9f3726d228f6fc6dccfcb2c45c5083f38b3ab8fe"),
        ("billers", "c988ce4542fbb39eb7900ea419483b9d3797aaab5873733cbf27db0920796dd8"),
        ("holdings", "191d8c89e451932e8de9b51ca5bfaa5dc7e95e9be1edfca3c1c76af0f449fb22"),
        ("transaction_history", "3103b840489a7d2878fc06090dce9a2ae723bee98b37b72c00e0519d73f2d71a"),
        ("cashflow_schedule", "4262976b2c18b8490ea202a6c162db6ca078e7f6083452bb13b8038527415a52"),
        ("quotes", "2491637a30ba0d8441ae5a6b3199eb44f2befdbac375d758915a020a31cf7397"),
        ("fx_rates", "8a79a487c1f651ce93fc91564973cb451af54f20ab531007ccaa07ad9f15a485"),
    ],
)
def test_expansion_preserves_original_records_exactly(
    bank_seed: dict[str, Any], section: str, expected: str
) -> None:
    """Golden digests from the reviewed pre-expansion seed, including internal fields."""
    user = bank_seed["users"]["u_demo"]
    if section == "quotes":
        original = {
            ticker: bank_seed["quotes"][ticker]
            for ticker in ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA")
        }
    elif section == "fx_rates":
        original = bank_seed[section]
    else:
        original = user[section]
        count = {"payees": 4, "holdings": 2, "transaction_history": 12}.get(section)
        if count is not None:
            original = original[:count]
    # New descriptive metadata must not alter any original financial or identity fields.
    if section in {"accounts", "transaction_history"}:
        original = [
            {k: v for k, v in row.items() if k not in {"reserve", "category", "is_spending"}}
            for row in original
        ]
    payload = json.dumps(original, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(payload.encode()).hexdigest() == expected
    assert user["display_name"] == "Alex Demo"


def test_expansion_adds_only_the_approved_fictional_instruments_and_payees(
    bank_seed: dict[str, Any],
) -> None:
    assert set(bank_seed["quotes"]) == {
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "NVDA",
        "TSLA",
        "META",
        "AMD",
    }
    for ticker, price in {"TSLA": "251.00", "META": "575.00", "AMD": "174.20"}.items():
        quote = bank_seed["quotes"][ticker]
        assert quote["ticker"] == ticker and quote["price"] == price
        assert quote["currency"] == "USD"
        assert quote["as_of"] == "2026-10-09T16:00:00-04:00"
        assert ticker in quote["aliases"]
    assert "synthetic" in bank_seed["description"]
    additions = bank_seed["users"]["u_demo"]["payees"][4:]
    assert {p["payee_id"]: p["name"] for p in additions} == {
        "pay_3101": "Aisha Rahman",
        "pay_3102": "Ben Lim",
        "pay_3103": "Chloe Tan",
        "pay_3104": "Ravi Kumar",
        "pay_3105": "Example Demo Landlord",
        "pay_3106": "Example Demo Utilities",
    }
    for payee in additions:
        assert payee["currency"] == "SGD" and payee["status"] == "active"
        assert payee["name"] in payee["aliases"] and payee["nickname"] in payee["aliases"]


def test_expansion_portfolio_keeps_cash_separate_and_matches_purchase_history(
    bank_seed: dict[str, Any],
) -> None:
    user = bank_seed["users"]["u_demo"]
    assert len(user["holdings"]) == 6
    assert {h["ticker"]: h["quantity"] for h in user["holdings"]} == {
        "AAPL": 10,
        "MSFT": 5,
        "NVDA": 12,
        "AMZN": 8,
        "GOOGL": 6,
        "AMD": 15,
    }
    assert all(h["account_id"] == "acc_7788" for h in user["holdings"])
    assert (
        next(a["balance"] for a in user["accounts"] if a["account_id"] == "acc_7788") == "30000.00"
    )
    purchases = user["transaction_history"][12:16]
    assert [(t["ticker"], t["quantity"], t["amount"]) for t in purchases] == [
        ("NVDA", 12, "1507.20"),
        ("AMZN", 8, "1490.40"),
        ("GOOGL", 6, "1010.40"),
        ("AMD", 15, "2613.00"),
    ]
    for purchase in purchases:
        assert purchase["account_id"] == "acc_7788" and purchase["direction"] == "debit"
        assert purchase["currency"] == "USD"
        assert (
            Decimal(purchase["amount"])
            == Decimal(bank_seed["quotes"][purchase["ticker"]]["price"]) * purchase["quantity"]
        )


def test_expansion_history_is_completed_and_does_not_settle_future_occurrences(
    bank_seed: dict[str, Any],
) -> None:
    user = bank_seed["users"]["u_demo"]
    additions = user["transaction_history"][12:]
    assert [t["transaction_id"] for t in additions] == [f"tx_seed_{i:04d}" for i in range(13, 20)]
    assert [t["reference"] for t in additions] == [f"SEED-{i:06d}" for i in range(13, 20)]
    dates = [datetime.fromisoformat(t["time"]) for t in additions]
    assert dates == sorted(dates)
    assert all(
        d.utcoffset() is not None and d < datetime.fromisoformat("2026-10-15T09:00:00+08:00")
        for d in dates
    )
    assert all(t["status"] == "completed" for t in additions)
    salary, rent, utilities = additions[4:]
    assert (salary["account_id"], salary["currency"], salary["direction"], salary["amount"]) == (
        "acc_5678",
        "SGD",
        "credit",
        "6200.00",
    )
    assert (rent["account_id"], rent["payee_id"], rent["amount"]) == (
        "acc_1234",
        "pay_3105",
        "1450.00",
    )
    assert (utilities["account_id"], utilities["payee_id"], utilities["amount"]) == (
        "acc_1234",
        "pay_3106",
        "92.40",
    )
    assert all(t["direction"] == "debit" and t["currency"] == "SGD" for t in (rent, utilities))
    assert [r["state"] for r in user["cashflow_schedule"]] == [
        "scheduled",
        "scheduled",
        "scheduled",
        "confirmed_demo",
    ]
    assert user["billers"][0]["payment_state"] == "outstanding"


@pytest.mark.parametrize(
    "account_id,deduction,income,commitments,before,after",
    [
        ("acc_1234", "0.00", "0.00", "2782.40", "417.60", "417.60"),
        ("acc_1234", "500.00", "0.00", "2782.40", "417.60", "-82.40"),
        ("acc_5678", "0.00", "6200.00", "0.00", "71200.00", "71200.00"),
        ("acc_7788", "0.00", "0.00", "0.00", "30000.00", "30000.00"),
    ],
)
def test_expansion_forecasts_preserve_the_account_specific_demo_story(
    bank_seed: dict[str, Any],
    account_id: str,
    deduction: str,
    income: str,
    commitments: str,
    before: str,
    after: str,
) -> None:
    user = bank_seed["users"]["u_demo"]
    original = copy.deepcopy(user)
    result = readonly.get_cashflow_forecast(
        "u_demo",
        account_id,
        as_of=datetime.fromisoformat("2026-10-15T09:00:00+08:00"),
        horizon_days=30,
        proposed_amount=Decimal(deduction),
        runtime_snapshot=user,
    )
    assert result["scheduled_income_total"] == income
    assert result["commitment_total"] == commitments
    assert (
        result["projected_balance_before"] == before and result["projected_balance_after"] == after
    )
    assert result["window_end"] == "2026-11-14T09:00:00+08:00"
    assert user == original
    if account_id == "acc_1234":
        assert [r["occurrence_id"] for r in result["commitments"]] == [
            "occ_card_2026_10",
            "occ_rent_2026_11",
            "occ_utilities_2026_11",
        ]
        assert result["currency"] == "SGD" and result["current_balance"] == "3200.00"


def test_expansion_history_is_never_replayed_into_opening_runtime_cash(
    ledger_store: dict[str, Path],
) -> None:
    state = _ledger_state(ledger_store)
    assert {a["account_id"]: a["balance"] for a in state["users"]["u_demo"]["accounts"]} == {
        "acc_1234": "3200.00",
        "acc_5678": "65000.00",
        "acc_7788": "30000.00",
    }
    assert len(state["users"]["u_demo"]["transaction_history"]) == 19
    assert state["transactions"] == [] and state["executions"] == {}


def test_expansion_never_silently_migrates_an_established_customer_snapshot(
    integrated_views: dict[str, Path],
) -> None:
    ledger.reconcile_audit(**integrated_views)
    state = _ledger_state(integrated_views)
    user = state["users"]["u_demo"]
    # Model an older installation entirely in temporary storage, with no financial reset.
    user["payees"] = user["payees"][:4]
    user["holdings"] = user["holdings"][:2]
    user["transaction_history"] = user["transaction_history"][:12]
    ledger._save_state(integrated_views["storage_path"], state)
    before = integrated_views["storage_path"].read_bytes()
    view = readonly.get_accounts("u_demo")
    assert len(view.payees) == 4 and len(view.holdings) == 2
    assert view.accounts[2].balance == Decimal("30000.00")
    assert readonly.get_quote("AMD").price == Decimal("174.20")
    assert integrated_views["storage_path"].read_bytes() == before


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "AMD"])
def test_expansion_quotes_do_not_authorise_stock_execution(
    risk_execution_store: dict[str, Path],
    bank_seed: dict[str, Any],
    ticker: str,
) -> None:
    quote = bank_seed["quotes"][ticker]
    draft = _ledger_draft(
        _ledger_action("stock_order", ticker=ticker, company_name=quote["company_name"], quantity=1)
    )
    before = _financial_state(risk_execution_store)
    assert (
        _risk_execution_result(risk_execution_store, "ex_expanded_stock", draft)
        == "policy_unsupported"
    )
    assert _financial_state(risk_execution_store) == before
    assert _ledger_state(risk_execution_store)["transactions"] == []


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
def account_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Explicitly initialise isolated opening state before testing public reads."""
    paths = {
        "storage_path": tmp_path / "views" / "ledger.json",
        "audit_path": tmp_path / "views" / "audit.jsonl",
    }
    monkeypatch.setattr(readonly, "_ledger_path", lambda path: path or paths["storage_path"])
    ledger.reconcile_audit(**paths)
    return paths


@pytest.fixture
def bank_client(account_storage: dict[str, Path]) -> TestClient:
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


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "AMD"])
def test_quote_endpoint_returns_requested_seed_quote(
    bank_client: TestClient, bank_seed: dict[str, Any], ticker: str
) -> None:
    response = bank_client.get("/api/quote", params={"ticker": ticker})
    assert response.status_code == 200
    fields = {"ticker", "company_name", "price", "currency", "as_of"}
    assert response.json() == {key: bank_seed["quotes"][ticker][key] for key in fields}
    assert re.fullmatch(r"\d+\.\d{2}", response.json()["price"])
    assert isinstance(readonly.get_quote(ticker).price, Decimal)


def test_readonly_serves_the_intent_engine(bank_seed: dict[str, Any]) -> None:
    assert [q.ticker for q in readonly.list_quotes()] == list(bank_seed["quotes"])
    assert readonly.get_bill_reference("bil_0001") == "4120-XXXX-XXXX-9012"
    with pytest.raises(LookupError):
        readonly.get_bill_reference("bil_9999")
    with pytest.raises(LookupError):
        readonly.get_fx_rate("SGD", "EUR")


def test_fx_endpoint_returns_seed_rates(bank_client: TestClient) -> None:
    response = bank_client.get("/api/fx", params={"from": "SGD", "to": "USD"})
    assert response.json() == {
        "from": "SGD",
        "to": "USD",
        "rate": "0.740000",
        "as_of": "2026-10-13T09:30:00+08:00",
    }
    assert bank_client.get("/api/fx", params={"from": "SGD", "to": "EUR"}).status_code == 404


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
    # Opening balances come from the seed during supervised initialisation only.
    paths = {
        "storage_path": tmp_path / "changed" / "ledger.json",
        "audit_path": tmp_path / "changed" / "audit.jsonl",
    }
    ledger.reconcile_audit(**paths)
    monkeypatch.setattr(readonly, "_ledger_path", lambda path: path or paths["storage_path"])
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
    account_storage: dict[str, Path],
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
    with pytest.raises(HTTPException) as error:
        readonly.get_accounts("u_demo")
    assert error.value.status_code == 503
    with pytest.raises((OSError, ValueError)):
        readonly.get_quote("AAPL")


@pytest.mark.parametrize("field", ["balance", "price"])
def test_invalid_financial_seed_values_fail_validation(
    field: str,
    bank_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readonly, "_load_database", lambda: bank_seed)
    if field == "balance":
        bank_seed["users"]["u_demo"]["accounts"][0][field] = "invalid"
        monkeypatch.setattr(readonly, "_current_user", lambda user_id: bank_seed["users"][user_id])
    else:
        bank_seed["quotes"]["AAPL"][field] = "invalid"
    with pytest.raises(ValidationError):
        if field == "balance":
            readonly.get_accounts("u_demo")
        else:
            readonly.get_quote("AAPL")


@pytest.fixture
def audit_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all runtime audit calls to temporary storage, including HTTP requests."""
    monkeypatch.setattr(audit, "__file__", str(tmp_path / "app" / "audit.py"))
    return tmp_path / "var" / "audit.jsonl"


def test_audit_records_are_linked_and_persistent(audit_path: Path) -> None:
    first = audit.append_event("validation", "validation_mismatch", {"draft_id": "d_demo"})
    second = audit.append_event("gateway", "signature_invalid", {"user_id": "u_demo"})
    assert first.index == 0
    assert second.index == 1
    assert second.prev_hash == first.hash
    assert audit.verify()
    assert first.time.utcoffset() is not None
    # A fresh module reload retains the same disk records; use explicit temporary path.
    importlib.reload(audit)
    assert audit.verify(storage_path=audit_path)
    assert len(audit.read_events(50, storage_path=audit_path).entries) == 2


@pytest.mark.parametrize("attack", ["amount", "reorder", "delete"])
def test_audit_tampering_is_detected_and_append_refused(
    audit_path: Path,
    bank_client: TestClient,
    attack: str,
) -> None:
    for number in range(3):
        audit.append_event("bank", "transaction_executed", {"amount": "50.00", "count": number})
    records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    if attack == "amount":
        records[0]["data"]["amount"] = "500.00"
    elif attack == "reorder":
        records[0], records[1] = records[1], records[0]
    else:
        del records[1]
    audit_path.write_text("".join(json.dumps(record) + "\n" for record in records))
    before = audit_path.read_bytes()
    assert audit.verify() is False
    response = bank_client.get("/api/audit?limit=1")
    assert response.status_code == 200
    assert response.json()["chain_valid"] is False
    assert len(response.json()["entries"]) == 1
    with pytest.raises(audit.AuditError):
        audit.append_event("gateway", "transaction_rejected", {})
    assert audit_path.read_bytes() == before


@pytest.mark.parametrize("content", ["broken\n", "{}\n", '{"index":0}', "\n"])
def test_malformed_or_interrupted_audit_log_fails_safely(audit_path: Path, content: str) -> None:
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(content)
    for operation in (
        audit.verify,
        lambda: audit.read_events(50),
        lambda: audit.append_event("gateway", "nonce_replay_detected", {}),
    ):
        with pytest.raises(audit.AuditError):
            operation()
    assert audit_path.read_text(encoding="utf-8") == content
    response = TestClient(app, raise_server_exceptions=False).get("/api/audit")
    assert response.status_code == 500
    assert content not in response.text


def test_audit_api_returns_real_public_records(audit_path: Path, bank_client: TestClient) -> None:
    assert bank_client.get("/api/audit").json() == {"chain_valid": True, "entries": []}
    for number in range(4):
        audit.append_event("gateway", "transaction_rejected", {"count": number})
    response = bank_client.get("/api/audit?limit=2")
    assert response.status_code == 200
    result = response.json()
    assert set(result) == {"chain_valid", "entries"}
    assert result["chain_valid"] is True
    assert [entry["index"] for entry in result["entries"]] == [2, 3]
    assert set(result["entries"][0]) == {
        "index",
        "time",
        "actor",
        "event",
        "data",
        "prev_hash",
        "hash",
    }


@pytest.mark.parametrize("limit", ["0", "-1", "abc", "1.5"])
def test_audit_api_rejects_invalid_limits(
    audit_path: Path,
    bank_client: TestClient,
    limit: str,
) -> None:
    response = bank_client.get("/api/audit", params={"limit": limit})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"


def test_concurrent_audit_appends_preserve_every_event(audit_path: Path) -> None:
    def append(number: int) -> None:
        audit.append_event("gateway", "signature_invalid", {"count": number})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(40)))
    assert audit.verify()
    entries = audit.read_events(50).entries
    assert len(entries) == 40
    assert {entry.data["count"] for entry in entries} == set(range(40))
    assert [entry.index for entry in entries] == list(range(40))


@pytest.mark.parametrize(
    "key", ["api_key", "password", "otp", "private_key", "biometric", "transcript", "nested"]
)
def test_audit_rejects_sensitive_or_unapproved_metadata(audit_path: Path, key: str) -> None:
    with pytest.raises(audit.AuditError) as error:
        audit.append_event("gateway", "signature_invalid", {key: "sensitive-test-value"})
    assert "sensitive-test-value" not in str(error.value)
    assert not audit_path.exists()


def test_audit_flushes_and_syncs_and_refuses_write_failures(
    audit_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    original = os.fsync
    calls = []

    def sync(descriptor: int) -> None:
        calls.append(descriptor)
        original(descriptor)

    monkeypatch.setattr(audit.os, "fsync", sync)
    audit.append_event("gateway", "kill_switch_activated", {"reason": "manual_pause"})
    assert len(calls) == (1 if os.name == "nt" else 2)

    def fail(descriptor: int) -> None:
        raise OSError("sensitive-test-value")

    monkeypatch.setattr(audit.os, "fsync", fail)
    with pytest.raises(audit.AuditError, match="Audit storage operation failed"):
        audit.append_event("gateway", "nonce_replay_detected", {})


def test_audit_hash_can_be_independently_recomputed(audit_path: Path) -> None:
    audit.append_event("validation", "validation_mismatch", {"amount": "50.00"})
    record = json.loads(audit_path.read_text(encoding="utf-8"))
    stored_hash = record.pop("hash")
    canonical = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == stored_hash


def test_separate_processes_append_under_the_same_lock(audit_path: Path) -> None:
    source = (
        "from pathlib import Path; import sys; from app.audit import append_event; "
        "[append_event('gateway', 'signature_invalid', {'count': int(sys.argv[2])}, "
        "storage_path=Path(sys.argv[1])) for _ in range(5)]"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", source, str(audit_path), str(number)],
            cwd=Path(__file__).resolve().parents[1],
        )
        for number in range(4)
    ]
    for process in processes:
        assert process.wait(timeout=30) == 0
    assert audit.verify()
    assert len(audit.read_events(50).entries) == 20


def test_user_input_cannot_change_audit_record_structure(audit_path: Path) -> None:
    with pytest.raises(audit.AuditError):
        audit.append_event("gateway", "signature_invalid\ntransaction_executed", {})
    with pytest.raises(audit.AuditError):
        audit.append_event("gateway", "signature_invalid", {"reason": {"otp": "secret"}})
    assert not audit_path.exists()


@pytest.fixture
def device_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account_storage: dict[str, Path]
) -> Path:
    """Keep device registration tests away from real runtime state."""
    monkeypatch.setattr(devices, "__file__", str(tmp_path / "app" / "bank" / "devices.py"))
    return tmp_path / "var" / "devices.json"


@pytest.fixture
def public_jwk() -> dict[str, Any]:
    """Use the public P-256 generator point; no private key is stored or required."""
    x = bytes.fromhex("6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296")
    y = bytes.fromhex("4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5")
    return {
        "kty": "EC",
        "crv": "P-256",
        "ext": True,
        "key_ops": ["verify"],
        "x": base64.urlsafe_b64encode(x).rstrip(b"=").decode(),
        "y": base64.urlsafe_b64encode(y).rstrip(b"=").decode(),
    }


def _other_public_jwk(public_jwk: dict[str, Any]) -> dict[str, Any]:
    key = dict(public_jwk)
    prime = int("ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 16)
    y = int.from_bytes(base64.urlsafe_b64decode(key["y"] + "="), "big")
    key["y"] = base64.urlsafe_b64encode((prime - y).to_bytes(32, "big")).rstrip(b"=").decode()
    return key


def test_device_registration_persists_only_public_coordinates(
    device_path: Path,
    public_jwk: dict[str, Any],
    bank_client: TestClient,
) -> None:
    assert devices.get_public_key("u_demo") is None
    response = bank_client.post(
        "/api/device/register", json={"user_id": "u_demo", "public_key_jwk": public_jwk}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "registered"}
    expected = {key: public_jwk[key] for key in ("kty", "crv", "x", "y")}
    assert devices.get_public_key("u_demo") == expected
    assert json.loads(device_path.read_text(encoding="utf-8")) == {"u_demo": expected}
    importlib.reload(devices)
    assert devices.get_public_key("u_demo", storage_path=device_path) == expected


@pytest.mark.parametrize(
    "patch",
    [
        {"kty": "RSA"},
        {"crv": "P-384"},
        {"d": "private-test-value"},
        {"private_key": "private-test-value"},
        {"x": "!" * 43},
        {"x": "AA"},
        {"x": None},
        {"x": 123},
        {"y": "A" * 43},
        {"x": "_" * 42 + "8"},
        {"x": "A" * 42 + "B"},
        {"key_ops": ["sign"]},
        {"ext": "true"},
        {"use": "enc"},
        {"alg": "ES384"},
        {"kid": {}},
    ],
)
def test_device_registration_rejects_invalid_keys_without_writing(
    device_path: Path,
    public_jwk: dict[str, Any],
    bank_client: TestClient,
    patch: dict[str, Any],
) -> None:
    response = bank_client.post(
        "/api/device/register", json={"user_id": "u_demo", "public_key_jwk": public_jwk | patch}
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "bad_request", "message": "Invalid ECDSA P-256 public key."}
    }
    assert not device_path.exists()


@pytest.mark.parametrize("field", ["kty", "crv", "x", "y"])
def test_device_registration_requires_all_key_fields(
    device_path: Path,
    public_jwk: dict[str, Any],
    bank_client: TestClient,
    field: str,
) -> None:
    del public_jwk[field]
    response = bank_client.post(
        "/api/device/register", json={"user_id": "u_demo", "public_key_jwk": public_jwk}
    )
    assert response.status_code == 400
    assert not device_path.exists()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"user_id": "u_demo"},
        {"public_key_jwk": {}},
        {"user_id": "u_demo", "public_key_jwk": []},
    ],
)
def test_device_endpoint_requires_request_fields(
    device_path: Path,
    bank_client: TestClient,
    body: dict[str, Any],
) -> None:
    response = bank_client.post("/api/device/register", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    assert not device_path.exists()


def test_device_unknown_user_is_not_registered(
    device_path: Path,
    public_jwk: dict[str, Any],
    bank_client: TestClient,
) -> None:
    response = bank_client.post(
        "/api/device/register", json={"user_id": "u_missing", "public_key_jwk": public_jwk}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    with pytest.raises(HTTPException) as error:
        devices.get_public_key("u_missing")
    assert error.value.status_code == 404
    assert not device_path.exists()


def test_device_repeat_is_idempotent_and_replacement_is_refused(
    device_path: Path,
    public_jwk: dict[str, Any],
    bank_client: TestClient,
) -> None:
    devices.register_public_key("u_demo", public_jwk)
    before = device_path.read_bytes()
    minimal = {key: public_jwk[key] for key in ("kty", "crv", "x", "y")}
    assert devices.register_public_key("u_demo", minimal).status == "registered"
    assert device_path.read_bytes() == before
    for key in (_other_public_jwk(public_jwk), public_jwk | {"d": "secret"}):
        response = bank_client.post(
            "/api/device/register", json={"user_id": "u_demo", "public_key_jwk": key}
        )
        assert response.status_code == 400
        assert device_path.read_bytes() == before
    retrieved = devices.get_public_key("u_demo")
    retrieved["x"] = "changed"
    assert devices.get_public_key("u_demo") == minimal


@pytest.mark.parametrize(
    "content", ["", "broken", "[]", '{"u_demo":{}}', '{"u_demo":{},"u_demo":{}}']
)
def test_corrupt_device_registry_is_never_reset(
    device_path: Path,
    public_jwk: dict[str, Any],
    content: str,
) -> None:
    device_path.parent.mkdir(parents=True)
    device_path.write_text(content)
    with pytest.raises(devices.DeviceStorageError):
        devices.get_public_key("u_demo")
    with pytest.raises(devices.DeviceStorageError):
        devices.register_public_key("u_demo", public_jwk)
    assert device_path.read_text(encoding="utf-8") == content
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/device/register", json={"user_id": "u_demo", "public_key_jwk": public_jwk}
    )
    assert response.status_code == 500
    assert response.text == "Internal Server Error"


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_device_storage_failure_preserves_existing_registry(
    device_path: Path,
    public_jwk: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    device_path.parent.mkdir(parents=True)
    device_path.write_text("{}\n")

    def fail(*arguments: Any) -> None:
        raise OSError("secret-test-value")

    monkeypatch.setattr(devices.os, failure, fail)
    with pytest.raises(devices.DeviceStorageError) as error:
        devices.register_public_key("u_demo", public_jwk)
    assert "secret-test-value" not in str(error.value)
    assert device_path.read_text(encoding="utf-8") == "{}\n"
    assert not list(device_path.parent.glob("*.tmp"))


def test_concurrent_device_registrations_pin_one_key(
    device_path: Path,
    public_jwk: dict[str, Any],
    bank_client: TestClient,
) -> None:
    other = _other_public_jwk(public_jwk)

    def register(number: int) -> int:
        return bank_client.post(
            "/api/device/register",
            json={"user_id": "u_demo", "public_key_jwk": public_jwk if number % 2 else other},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(register, range(20)))
    assert statuses.count(200) == 10
    assert statuses.count(400) == 10
    stored = devices.get_public_key("u_demo")
    assert stored in [{k: key[k] for k in ("kty", "crv", "x", "y")} for key in (public_jwk, other)]


def test_device_process_lock_prevents_competing_key_replacement(
    device_path: Path,
    public_jwk: dict[str, Any],
    account_storage: dict[str, Path],
) -> None:
    source = (
        "import sys,json; from pathlib import Path; from fastapi import HTTPException; "
        "from app.bank.devices import register_public_key\n"
        "from app.bank import readonly\n"
        "readonly._ledger_path = lambda path: path or Path(sys.argv[3])\n"
        "try:\n register_public_key('u_demo', json.loads(sys.argv[2]), "
        "storage_path=Path(sys.argv[1]))\n"
        "except HTTPException as error:\n sys.exit(2 if error.status_code == 400 else 3)\n"
    )
    keys = [public_jwk, _other_public_jwk(public_jwk)] * 2
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                source,
                str(device_path),
                json.dumps(key),
                str(account_storage["storage_path"]),
            ],
            cwd=Path(__file__).resolve().parents[1],
        )
        for key in keys
    ]
    statuses = [process.wait(timeout=30) for process in processes]
    assert sorted(statuses) == [0, 0, 2, 2]
    assert devices.get_public_key("u_demo") is not None


def test_device_registry_invalid_encoding_is_sanitized(
    device_path: Path,
    public_jwk: dict[str, Any],
) -> None:
    device_path.parent.mkdir(parents=True)
    device_path.write_bytes(b"\xff\xfe")
    with pytest.raises(devices.DeviceStorageError):
        devices.register_public_key("u_demo", public_jwk)
    assert device_path.read_bytes() == b"\xff\xfe"


def test_device_registry_read_failure_never_fabricates_an_empty_registry(
    device_path: Path,
    public_jwk: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.read_text

    def read(path: Path, *arguments: Any, **keywords: Any) -> str:
        if path == device_path:
            raise PermissionError("sensitive-test-value")
        return original(path, *arguments, **keywords)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(devices.DeviceStorageError, match="storage operation failed"):
        devices.register_public_key("u_demo", public_jwk)
    assert not device_path.exists()


@pytest.mark.skipif(devices.os.name == "nt", reason="Directory fsync is POSIX-only")
def test_device_directory_sync_failure_does_not_report_success(
    device_path: Path,
    public_jwk: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = devices.os.fsync
    calls = []

    def sync(descriptor: int) -> None:
        calls.append(descriptor)
        if len(calls) == 2:
            raise OSError("directory synchronization failed")
        original(descriptor)

    monkeypatch.setattr(devices.os, "fsync", sync)
    with pytest.raises(devices.DeviceStorageError):
        devices.register_public_key("u_demo", public_jwk)
    # Replace may have committed before directory fsync failed; the file stays whole.
    assert json.loads(device_path.read_text(encoding="utf-8"))["u_demo"]["x"] == public_jwk["x"]
    assert len(calls) == (1 if audit.os.name == "nt" else 2)
    assert not list(device_path.parent.glob("*.tmp"))


def test_device_storage_path_is_independent_of_working_directory(
    device_path: Path,
    public_jwk: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert devices.register_public_key("u_demo", public_jwk).status == "registered"
    assert device_path.is_file()
    assert devices.get_public_key("u_demo")["x"] == public_jwk["x"]


@pytest.fixture
def forecast_seed(bank_seed: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Use independent in-memory schedule data without touching the committed seed."""
    monkeypatch.setattr(readonly, "_load_database", lambda: bank_seed)
    monkeypatch.setattr(readonly, "_current_user", lambda user_id: bank_seed["users"][user_id])
    return bank_seed


def _demo_forecast(account_id: str = "acc_1234", amount: str = "500.00") -> dict[str, Any]:
    return readonly.get_cashflow_forecast(
        "u_demo",
        account_id,
        as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
        horizon_days=30,
        proposed_amount=Decimal(amount),
    )


def test_cashflow_demo_totals_and_negative_projection_are_advisory(
    forecast_seed: dict[str, Any],
) -> None:
    before = json.dumps(forecast_seed, sort_keys=True)
    forecast = _demo_forecast()
    assert forecast["current_balance"] == "3200.00"
    assert forecast["scheduled_income_total"] == "0.00"
    assert forecast["commitment_total"] == "2782.40"
    assert forecast["projected_balance_before"] == "417.60"
    assert forecast["projected_balance_after"] == "-82.40"
    assert forecast["transaction_difference"] == "-500.00"
    assert forecast["projected_shortfall"] == "82.40"
    assert [item["occurrence_id"] for item in forecast["commitments"]] == [
        "occ_card_2026_10",
        "occ_rent_2026_11",
        "occ_utilities_2026_11",
    ]
    assert forecast["estimate"] is True
    assert forecast["synthetic"] is True
    assert "externally verified" in " ".join(forecast["assumptions"])
    assert "other expenses" in " ".join(forecast["assumptions"])
    assert "status" not in forecast
    assert json.dumps(forecast_seed, sort_keys=True) == before


def test_cashflow_salary_is_a_separate_fictional_current_account_inflow(
    forecast_seed: dict[str, Any],
) -> None:
    result = _demo_forecast("acc_5678", "50.00")
    assert result["scheduled_income_total"] == "6200.00"
    assert result["commitment_total"] == "0.00"
    assert result["projected_balance_after"] == "71150.00"
    assert result["scheduled_income"][0]["synthetic"] is True
    assert _demo_forecast("acc_7788")["projected_balance_after"] == "29500.00"
    assert _demo_forecast("acc_7788")["currency"] == "USD"


def test_cashflow_paid_bill_is_excluded_despite_stale_schedule(
    forecast_seed: dict[str, Any],
) -> None:
    user = forecast_seed["users"]["u_demo"]
    user["billers"][0]["payment_state"] = "paid"
    result = _demo_forecast()
    assert result["commitment_total"] == "1542.40"
    assert len(result["commitments"]) == 2


def test_cashflow_empty_schedule_still_includes_known_outstanding_bill(
    forecast_seed: dict[str, Any],
) -> None:
    forecast_seed["users"]["u_demo"]["cashflow_schedule"] = []
    assert _demo_forecast()["commitment_total"] == "1240.00"


def test_cashflow_no_upcoming_commitments_requires_explicit_schedule(
    forecast_seed: dict[str, Any],
) -> None:
    user = forecast_seed["users"]["u_demo"]
    user["cashflow_schedule"] = []
    user["billers"][0]["payment_state"] = "paid"
    result = _demo_forecast()
    assert result["commitment_total"] == "0.00"
    assert result["projected_balance_after"] == "2700.00"
    assert result["commitments"] == []


@pytest.mark.parametrize("state", ["paid", "cancelled"])
def test_cashflow_paid_or_cancelled_recurring_expenses_are_excluded(
    forecast_seed: dict[str, Any],
    state: str,
) -> None:
    forecast_seed["users"]["u_demo"]["cashflow_schedule"][1]["state"] = state
    assert _demo_forecast()["commitment_total"] == "1332.40"


def test_cancelling_card_payment_does_not_erase_outstanding_bill(
    forecast_seed: dict[str, Any],
) -> None:
    forecast_seed["users"]["u_demo"]["cashflow_schedule"][0]["state"] = "cancelled"
    assert _demo_forecast()["commitment_total"] == "2782.40"


@pytest.mark.parametrize("state", ["received", "cancelled", "unconfirmed"])
def test_cashflow_only_explicit_demo_confirmed_income_is_counted(
    forecast_seed: dict[str, Any],
    state: str,
) -> None:
    forecast_seed["users"]["u_demo"]["cashflow_schedule"][3]["state"] = state
    assert _demo_forecast("acc_5678")["scheduled_income_total"] == "0.00"


@pytest.mark.parametrize("mutation", ["occurrence", "obligation"])
def test_cashflow_duplicate_payments_are_rejected_instead_of_double_counted(
    forecast_seed: dict[str, Any],
    mutation: str,
) -> None:
    schedule = forecast_seed["users"]["u_demo"]["cashflow_schedule"]
    duplicate = dict(schedule[0])
    if mutation == "obligation":
        duplicate["occurrence_id"] = "occ_duplicate"
    schedule.append(duplicate)
    with pytest.raises(readonly.ForecastDataError):
        _demo_forecast()


@pytest.mark.parametrize(
    "offset,expected", [(-1, "0.00"), (0, "1450.00"), (2591999, "1450.00"), (2592000, "0.00")]
)
def test_cashflow_date_window_is_start_inclusive_end_exclusive(
    forecast_seed: dict[str, Any],
    offset: int,
    expected: str,
) -> None:
    user = forecast_seed["users"]["u_demo"]
    item = dict(user["cashflow_schedule"][1])
    start = datetime.fromisoformat("2026-10-12T00:00:00+08:00")
    item["due_at"] = (start + timedelta(seconds=offset)).isoformat()
    user["cashflow_schedule"] = [item]
    user["billers"][0]["payment_state"] = "paid"
    assert _demo_forecast()["commitment_total"] == expected


def test_cashflow_dates_compare_absolute_instants_across_offsets(
    forecast_seed: dict[str, Any],
) -> None:
    user = forecast_seed["users"]["u_demo"]
    user["billers"][0]["payment_state"] = "paid"
    item = dict(user["cashflow_schedule"][1], due_at="2026-10-11T16:00:00+00:00")
    user["cashflow_schedule"] = [item]
    assert _demo_forecast()["commitment_total"] == "1450.00"


@pytest.mark.parametrize("schedule", [None, {}, "invalid"])
def test_cashflow_missing_or_invalid_schedule_is_not_zero_expenses(
    forecast_seed: dict[str, Any],
    schedule: Any,
) -> None:
    forecast_seed["users"]["u_demo"]["cashflow_schedule"] = schedule
    with pytest.raises(readonly.ForecastDataError, match="unavailable"):
        _demo_forecast()


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", "NaN"),
        ("amount", "-1.00"),
        ("amount", "12.345"),
        ("amount", 12.5),
        ("amount", "12"),
        ("due_at", "2026-11-01T00:00:00"),
        ("due_at", "bad"),
        ("state", "unknown"),
        ("kind", "unknown"),
        ("account_id", "acc_unknown"),
        ("currency", "USD"),
        ("synthetic", False),
    ],
)
def test_cashflow_invalid_occurrences_never_produce_a_projection(
    forecast_seed: dict[str, Any],
    field: str,
    value: Any,
) -> None:
    forecast_seed["users"]["u_demo"]["cashflow_schedule"][1][field] = value
    with pytest.raises(readonly.ForecastDataError):
        _demo_forecast()


@pytest.mark.parametrize(
    "field,value",
    [
        ("biller_id", "bil_unknown"),
        ("obligation_id", "obl_wrong"),
        ("account_id", "acc_5678"),
        ("amount", "10.00"),
        ("state", "paid"),
        ("due_at", "2026-10-21T00:00:00+08:00"),
    ],
)
def test_cashflow_bill_reference_and_payment_state_must_be_consistent(
    forecast_seed: dict[str, Any],
    field: str,
    value: Any,
) -> None:
    forecast_seed["users"]["u_demo"]["cashflow_schedule"][0][field] = value
    with pytest.raises(readonly.ForecastDataError):
        _demo_forecast()


@pytest.mark.parametrize("horizon", [0, -1, True, 30.5])
def test_cashflow_requires_explicit_positive_integer_horizon(
    forecast_seed: dict[str, Any],
    horizon: Any,
) -> None:
    with pytest.raises(readonly.ForecastDataError):
        readonly.get_cashflow_forecast(
            "u_demo",
            "acc_1234",
            as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
            horizon_days=horizon,
            proposed_amount=Decimal("0.00"),
        )


def test_cashflow_horizon_is_configurable_without_changing_config(
    forecast_seed: dict[str, Any],
) -> None:
    result = readonly.get_cashflow_forecast(
        "u_demo",
        "acc_1234",
        as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
        horizon_days=7,
        proposed_amount=Decimal("0"),
    )
    assert result["commitment_total"] == "0.00"


def test_cashflow_can_use_current_snapshot_without_ledger_access(
    forecast_seed: dict[str, Any],
) -> None:
    account = (
        readonly.get_accounts("u_demo")
        .accounts[0]
        .model_copy(update={"balance": Decimal("4000.00")})
    )
    result = readonly.get_cashflow_forecast(
        "u_demo",
        "acc_1234",
        as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
        horizon_days=30,
        proposed_amount=Decimal("500.00"),
        balance_snapshot=account,
    )
    assert result["current_balance"] == "4000.00"
    assert result["projected_balance_after"] == "717.60"
    wrong = account.model_copy(update={"currency": "USD"})
    with pytest.raises(readonly.ForecastDataError):
        readonly.get_cashflow_forecast(
            "u_demo",
            "acc_1234",
            as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
            horizon_days=30,
            proposed_amount=Decimal("500.00"),
            balance_snapshot=wrong,
        )


def test_pure_forecast_uses_exact_decimal_arithmetic_under_low_precision() -> None:
    account = Account(
        account_id="acc_test", label="Demo", type="savings", currency="SGD", balance="1234.56"
    )
    with localcontext() as context:
        context.prec = 2
        result = readonly.calculate_cashflow_forecast(
            account=account,
            occurrences=[],
            as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
            horizon_days=30,
            proposed_amount=Decimal("500.00"),
        )
    assert result["projected_balance_after"] == "734.56"


def test_cashflow_forecast_preserves_database_and_existing_api_responses(
    bank_client: TestClient,
) -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "bank.json"
    before = path.read_bytes()
    accounts = bank_client.get("/api/accounts?user_id=u_demo").json()
    quote = bank_client.get("/api/quote?ticker=AAPL").json()
    assert _demo_forecast()["projected_balance_after"] == "-82.40"
    assert path.read_bytes() == before
    assert bank_client.get("/api/accounts?user_id=u_demo").json() == accounts
    assert bank_client.get("/api/quote?ticker=AAPL").json() == quote


def test_forecasting_has_no_ai_or_execution_dependencies() -> None:
    tree = ast.parse(Path(readonly.__file__).read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    assert not any(
        name.startswith(
            (
                "app.intent",
                "app.llm",
                "app.validation",
                "app.bank.ledger",
                "app.bank.gateway",
                "httpx",
            )
        )
        for name in imports
    )


@pytest.mark.parametrize(
    "user_id,account_id", [("u_unknown", "acc_1234"), ("u_demo", "acc_unknown")]
)
def test_cashflow_unknown_users_or_accounts_return_not_found(
    user_id: str, account_id: str, account_storage: dict[str, Path]
) -> None:
    with pytest.raises(HTTPException) as error:
        readonly.get_cashflow_forecast(
            user_id,
            account_id,
            as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
            horizon_days=30,
            proposed_amount=Decimal("0.00"),
        )
    assert error.value.status_code == 404


def test_cashflow_never_opens_a_network_connection(
    monkeypatch: pytest.MonkeyPatch,
    account_storage: dict[str, Path],
) -> None:
    def forbidden(*arguments: Any, **keywords: Any) -> None:
        pytest.fail("Forecast attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    assert _demo_forecast()["projected_balance_after"] == "-82.40"


@pytest.mark.parametrize(
    "amount",
    [Decimal("-1.00"), Decimal("NaN"), Decimal("Infinity"), Decimal("0.001"), 500.0, "500.00"],
)
def test_cashflow_rejects_invalid_hypothetical_money(
    forecast_seed: dict[str, Any],
    amount: Any,
) -> None:
    with pytest.raises(readonly.ForecastDataError):
        readonly.get_cashflow_forecast(
            "u_demo",
            "acc_1234",
            as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
            horizon_days=30,
            proposed_amount=amount,
        )


def test_cashflow_requires_aware_as_of_and_does_not_repeat_monthly_occurrences(
    forecast_seed: dict[str, Any],
) -> None:
    with pytest.raises(readonly.ForecastDataError):
        readonly.get_cashflow_forecast(
            "u_demo",
            "acc_1234",
            as_of=datetime(2026, 10, 12),
            horizon_days=30,
            proposed_amount=Decimal("0.00"),
        )
    result = readonly.get_cashflow_forecast(
        "u_demo",
        "acc_1234",
        as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
        horizon_days=60,
        proposed_amount=Decimal("0.00"),
    )
    assert result["commitment_total"] == "2782.40"
    assert result["transaction_difference"] == "0.00"


def test_cashflow_currency_mismatch_in_pure_calculation_is_rejected() -> None:
    account = Account(
        account_id="acc_test", label="Demo", type="investment", currency="USD", balance="30000.00"
    )
    occurrence = {
        "occurrence_id": "occ_test",
        "account_id": "acc_test",
        "currency": "SGD",
        "kind": "expense",
        "state": "scheduled",
        "synthetic": True,
        "label": "Fictional expense",
        "amount": "50.00",
        "due_at": "2026-10-20T00:00:00+08:00",
    }
    with pytest.raises(readonly.ForecastDataError):
        readonly.calculate_cashflow_forecast(
            account=account,
            occurrences=[occurrence],
            as_of=datetime.fromisoformat("2026-10-12T00:00:00+08:00"),
            horizon_days=30,
            proposed_amount=Decimal("0.00"),
        )


def test_cashflow_history_does_not_change_opening_balances_or_scheduled_totals(
    forecast_seed: dict[str, Any],
) -> None:
    before = _demo_forecast()
    forecast_seed["users"]["u_demo"]["transaction_history"][5]["amount"] = "9999.00"
    assert _demo_forecast() == before


def test_cashflow_unknown_bill_payment_state_and_absent_schedule_fail_clearly(
    forecast_seed: dict[str, Any],
) -> None:
    user = forecast_seed["users"]["u_demo"]
    user["billers"][0].pop("payment_state")
    with pytest.raises(readonly.ForecastDataError):
        _demo_forecast()
    user.pop("cashflow_schedule")
    with pytest.raises(readonly.ForecastDataError, match="unavailable"):
        _demo_forecast()


@pytest.fixture
def ledger_paths(tmp_path: Path) -> dict[str, Path]:
    return {"storage_path": tmp_path / "ledger.json", "audit_path": tmp_path / "audit.jsonl"}


@pytest.fixture
def financial_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate existing J-08 financial/storage tests from unapproved live policies.

    Only explicitly marked older component tests use this risk-engine test double.
    Real mandatory-risk integration tests below use the actual evaluator and controls.
    Stock/bill/FX calculations remain tested here, without enabling those live policies.
    """
    monkeypatch.setattr(
        ledger,
        "rules",
        SimpleNamespace(
            execution_policy=lambda: None,
            evaluate_risk=lambda *args, **kwargs: rules.RiskDecision(
                status="allow", reason="allowed"
            ),
        ),
    )
    load = ledger._load_state

    def load_financial_fixture(*args: Any, **kwargs: Any) -> dict[str, Any]:
        state = load(*args, **kwargs)
        state["payment_control"] = {"paused": False}
        return state

    monkeypatch.setattr(ledger, "_load_state", load_financial_fixture)


_FINANCIAL_KERNEL_PROCESS = """
# Test-only financial-component isolation, identical to financial_kernel fixture.
from app.bank import ledger, rules
from types import SimpleNamespace
ledger.rules = SimpleNamespace(execution_policy=lambda: None,
    evaluate_risk=lambda *args, **kwargs: rules.RiskDecision(status="allow", reason="allowed"))
load = ledger._load_state
def load_financial_fixture(*args, **kwargs):
    state = load(*args, **kwargs)
    state["payment_control"] = {"paused": False}
    return state
ledger._load_state = load_financial_fixture
"""


@pytest.fixture
def ledger_store(ledger_paths: dict[str, Path]) -> dict[str, Path]:
    ledger.reconcile_audit(**ledger_paths)
    return ledger_paths


def _ledger_action(kind: str = "transfer", **changes: Any) -> dict[str, Any]:
    actions = {
        "transfer": {
            "type": "transfer",
            "from_account_id": "acc_1234",
            "from_account_label": "Savings …1234",
            "to_payee_id": "pay_4521",
            "to_payee_label": "John Doe …4521",
            "amount": "100.00",
            "currency": "SGD",
        },
        "bill_payment": {
            "type": "bill_payment",
            "from_account_id": "acc_1234",
            "from_account_label": "Savings …1234",
            "biller_id": "bil_0001",
            "biller_label": "DBS Credit Card …9012",
            "bill_reference": "4120-XXXX-XXXX-9012",
            "amount": "1240.00",
            "currency": "SGD",
        },
        "stock_order": {
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
        },
        "fx_conversion": {
            "type": "fx_conversion",
            "from_account_id": "acc_5678",
            "from_account_label": "Current …5678",
            "to_account_id": "acc_7788",
            "to_account_label": "Invest (USD) …7788",
            "sell_amount": "1000.00",
            "sell_currency": "SGD",
            "buy_amount": "740.00",
            "buy_currency": "USD",
            "rate": "0.740000",
            "rate_as_of": "2026-10-13T09:30:00+08:00",
        },
    }
    return {**actions[kind], **changes}


def _ledger_draft(*actions: dict[str, Any], **changes: Any) -> Draft:
    return Draft.model_validate(
        {
            "draft_id": "d_ledger_demo",
            "user_id": "u_demo",
            "session_id": "s_ledger_demo",
            "transcript": "Fictional ledger test instruction",
            "created_at": "2026-10-13T10:00:00+08:00",
            "actions": list(actions) or [_ledger_action()],
            "evidence": [],
            **changes,
        }
    )


def _ledger_state(paths: dict[str, Path]) -> dict[str, Any]:
    return readonly._read_ledger_state(paths["storage_path"])


def _ledger_balance(paths: dict[str, Path], account_id: str = "acc_1234") -> str:
    user = readonly.get_ledger_snapshot("u_demo", storage_path=paths["storage_path"])
    return next(a["balance"] for a in user["accounts"] if a["account_id"] == account_id)


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_transfer_commits_seed_snapshot_and_minimal_audit(
    ledger_paths: dict[str, Path],
    bank_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_path = Path(__file__).resolve().parents[1] / "data" / "bank.json"
    original = seed_path.read_bytes()
    response = ledger.execute_batch(_ledger_draft(), "ex_transfer", **ledger_paths)
    assert response.model_dump(mode="json") == {
        "status": "executed",
        "results": [{"action_index": 0, "reference": "tx_00000001"}],
    }
    state = _ledger_state(ledger_paths)
    assert _ledger_balance(ledger_paths) == "3100.00"
    assert _ledger_balance(ledger_paths, "acc_5678") == "65000.00"
    assert (
        state["users"]["u_demo"]["transaction_history"]
        == bank_seed["users"]["u_demo"]["transaction_history"]
    )
    assert len(state["transactions"]) == 1
    assert state["transactions"][0]["action"] == _ledger_action()
    assert datetime.fromisoformat(state["transactions"][0]["completed_at"]).utcoffset() is not None
    assert state["outbox"] == []
    events = audit.read_events(100, storage_path=ledger_paths["audit_path"])
    assert events.chain_valid
    assert events.entries[0].event == "transaction_executed"
    assert set(events.entries[0].data) == {"transaction_id", "count"}
    assert seed_path.read_bytes() == original
    monkeypatch.setattr(readonly, "_ledger_path", lambda path: path or ledger_paths["storage_path"])
    assert readonly.get_accounts("u_demo").accounts[0].balance == Decimal("3100.00")


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_partial_and_full_bill_payments_update_forecast(
    ledger_store: dict[str, Path],
) -> None:
    ledger.execute_batch(
        _ledger_draft(_ledger_action("bill_payment", amount="240.00")), "ex_partial", **ledger_store
    )
    partial = readonly.get_ledger_snapshot("u_demo", storage_path=ledger_store["storage_path"])
    assert partial["billers"][0]["amount_due"] == "1000.00"
    assert partial["billers"][0]["payment_state"] == "outstanding"
    forecast_args = {
        "as_of": datetime.fromisoformat("2026-10-13T00:00:00+08:00"),
        "horizon_days": 30,
        "proposed_amount": Decimal("0.00"),
    }
    partial_forecast = readonly.get_cashflow_forecast(
        "u_demo", "acc_1234", runtime_snapshot=partial, **forecast_args
    )
    assert partial_forecast["commitment_total"] == "2542.40"
    ledger.execute_batch(
        _ledger_draft(_ledger_action("bill_payment", amount="1000.00")), "ex_settle", **ledger_store
    )
    settled = readonly.get_ledger_snapshot("u_demo", storage_path=ledger_store["storage_path"])
    assert settled["billers"][0]["amount_due"] == "0.00"
    assert settled["billers"][0]["payment_state"] == "paid"
    assert settled["cashflow_schedule"][0]["state"] == "paid"
    forecast = readonly.get_cashflow_forecast(
        "u_demo", "acc_1234", runtime_snapshot=settled, **forecast_args
    )
    assert forecast["current_balance"] == "1960.00"
    assert forecast["commitment_total"] == "1542.40"
    assert not any(
        "biller" in c["occurrence_id"] or c["amount"] == "1240.00" for c in forecast["commitments"]
    )
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="bill_settled"):
        ledger.execute_batch(
            _ledger_draft(_ledger_action("bill_payment")), "ex_again", **ledger_store
        )
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_stock_buy_sell_and_execution_details(
    ledger_store: dict[str, Path], bank_seed: dict[str, Any]
) -> None:
    ledger.execute_batch(_ledger_draft(_ledger_action("stock_order")), "ex_buy", **ledger_store)
    assert _ledger_balance(ledger_store, "acc_7788") == "7250.00"
    user = readonly.get_ledger_snapshot("u_demo", storage_path=ledger_store["storage_path"])
    assert next(h["quantity"] for h in user["holdings"] if h["ticker"] == "AAPL") == 110
    details = _ledger_state(ledger_store)["transactions"][0]["execution_details"]
    assert details == {
        "execution_price": "227.50",
        "gross_amount": "22750.00",
        "quote_as_of": bank_seed["quotes"]["AAPL"]["as_of"],
    }
    ledger.execute_batch(
        _ledger_draft(_ledger_action("stock_order", side="sell", quantity=110)),
        "ex_sell",
        **ledger_store,
    )
    assert _ledger_balance(ledger_store, "acc_7788") == "32275.00"
    user = readonly.get_ledger_snapshot("u_demo", storage_path=ledger_store["storage_path"])
    assert next(h["quantity"] for h in user["holdings"] if h["ticker"] == "AAPL") == 0


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "AMD"])
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_buys_all_seed_stocks(
    ticker: str,
    ledger_store: dict[str, Path],
    bank_seed: dict[str, Any],
) -> None:
    quote = bank_seed["quotes"][ticker]
    ledger.execute_batch(
        _ledger_draft(
            _ledger_action(
                "stock_order", ticker=ticker, company_name=quote["company_name"], quantity=1
            )
        ),
        "ex_stock",
        **ledger_store,
    )
    assert Decimal(_ledger_balance(ledger_store, "acc_7788")) == Decimal("30000.00") - Decimal(
        quote["price"]
    )


@pytest.mark.parametrize(
    "side,limit,accepted",
    [
        ("buy", "228.00", True),
        ("buy", "227.50", True),
        ("buy", "227.49", False),
        ("sell", "227.50", True),
        ("sell", "228.00", False),
    ],
)
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_limit_orders_use_quote_and_never_leave_pending_orders(
    side: str,
    limit: str,
    accepted: bool,
    ledger_store: dict[str, Path],
) -> None:
    draft = _ledger_draft(
        _ledger_action("stock_order", side=side, quantity=1, order_type="limit", limit_price=limit)
    )
    before = ledger_store["storage_path"].read_bytes()
    if accepted:
        ledger.execute_batch(draft, "ex_limit", **ledger_store)
        assert (
            _ledger_state(ledger_store)["transactions"][0]["execution_details"]["execution_price"]
            == "227.50"
        )
    else:
        with pytest.raises(ledger.LedgerRejected, match="order_not_executable"):
            ledger.execute_batch(draft, "ex_limit", **ledger_store)
        assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_fx_direction_rounding_and_records(ledger_store: dict[str, Path]) -> None:
    action = _ledger_action("fx_conversion", sell_amount="1000.01")
    ledger.execute_batch(_ledger_draft(action), "ex_fx", **ledger_store)
    assert _ledger_balance(ledger_store, "acc_5678") == "63999.99"
    assert _ledger_balance(ledger_store, "acc_7788") == "30740.00"
    transaction = _ledger_state(ledger_store)["transactions"][0]
    assert transaction["action"] == action
    assert transaction["execution_details"]["rounding"] == "ROUND_DOWN"
    reverse = _ledger_action(
        "fx_conversion",
        from_account_id="acc_7788",
        from_account_label="Invest (USD) …7788",
        to_account_id="acc_5678",
        to_account_label="Current …5678",
        sell_currency="USD",
        buy_currency="SGD",
        sell_amount="100.00",
        buy_amount="135.13",
        rate="1.351351",
    )
    ledger.execute_batch(_ledger_draft(reverse), "ex_reverse", **ledger_store)
    assert _ledger_balance(ledger_store, "acc_5678") == "64135.12"
    assert _ledger_balance(ledger_store, "acc_7788") == "30640.00"


@pytest.mark.parametrize(
    "action,reason",
    [
        (_ledger_action(from_account_id="acc_foreign"), "invalid_reference"),
        (_ledger_action(currency="USD"), "invalid_currency"),
        (_ledger_action(from_account_label="Fake account"), "label_mismatch"),
        (_ledger_action(to_payee_id="pay_unknown"), "invalid_reference"),
        (_ledger_action(to_payee_label="John Smith …8892"), "label_mismatch"),
        (_ledger_action(amount="3200.01"), "insufficient_funds"),
        (_ledger_action(amount="0.00"), "invalid_amount"),
        (
            _ledger_action(
                from_account_id="acc_7788", from_account_label="Invest (USD) …7788", currency="USD"
            ),
            "invalid_account_type",
        ),
        (_ledger_action("bill_payment", bill_reference="bad"), "invalid_reference"),
        (_ledger_action("bill_payment", amount="1240.01"), "bill_overpayment"),
        (_ledger_action("stock_order", side="sell", quantity=11), "insufficient_holdings"),
        (_ledger_action("stock_order", quantity=132), "insufficient_funds"),
        (_ledger_action("stock_order", company_name="Microsoft Corporation"), "label_mismatch"),
        (_ledger_action("stock_order", ticker="UNKNOWN"), "invalid_ticker"),
        (
            _ledger_action(
                "stock_order",
                from_account_id="acc_1234",
                from_account_label="Savings …1234",
                currency="SGD",
            ),
            "invalid_account_type",
        ),
        (_ledger_action("fx_conversion", buy_amount="740.01"), "rate_changed"),
        (_ledger_action("fx_conversion", rate="0.750000"), "rate_changed"),
        (_ledger_action("fx_conversion", rate_as_of="2026-10-12T09:30:00+08:00"), "rate_changed"),
        (_ledger_action("fx_conversion", to_account_id="acc_foreign"), "invalid_reference"),
        (
            _ledger_action(
                "fx_conversion", to_account_id="acc_1234", to_account_label="Savings …1234"
            ),
            "invalid_currency",
        ),
        (
            _ledger_action("fx_conversion", sell_amount="100000.00", buy_amount="74000.00"),
            "insufficient_funds",
        ),
    ],
)
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_invalid_actions_reject_without_any_financial_changes(
    action: dict[str, Any],
    reason: str,
    ledger_store: dict[str, Path],
) -> None:
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected) as error:
        ledger.execute_batch(_ledger_draft(action), "ex_attack", **ledger_store)
    assert error.value.reason == reason
    assert ledger_store["storage_path"].read_bytes() == before
    entry = audit.read_events(1, storage_path=ledger_store["audit_path"]).entries[0]
    assert entry.event == "transaction_rejected"
    assert entry.data["reason"] == reason


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_unknown_user_cannot_use_another_users_accounts(
    ledger_store: dict[str, Path],
) -> None:
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="unknown_user"):
        ledger.execute_batch(_ledger_draft(user_id="u_other"), "ex_foreign", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_failed_final_action_rolls_back_bills_stocks_fx_and_history(
    ledger_store: dict[str, Path],
) -> None:
    before = ledger_store["storage_path"].read_bytes()
    draft = _ledger_draft(
        _ledger_action("bill_payment"),
        _ledger_action("stock_order", quantity=1),
        _ledger_action("fx_conversion"),
        _ledger_action(amount="2000.00"),
    )
    with pytest.raises(ledger.LedgerRejected, match="insufficient_funds"):
        ledger.execute_batch(draft, "ex_rollback", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_ordered_batch_can_fund_stock_purchase_with_explicit_fx(
    ledger_store: dict[str, Path],
) -> None:
    fx = _ledger_action("fx_conversion", sell_amount="10000.00", buy_amount="7400.00")
    stock = _ledger_action("stock_order", quantity=160)
    response = ledger.execute_batch(_ledger_draft(fx, stock), "ex_funded_buy", **ledger_store)
    assert [result.action_index for result in response.results] == [0, 1]
    assert _ledger_balance(ledger_store, "acc_5678") == "55000.00"
    assert _ledger_balance(ledger_store, "acc_7788") == "1000.00"


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_same_draft_retry_returns_original_response_after_reload(
    ledger_store: dict[str, Path],
) -> None:
    draft = _ledger_draft()
    first = ledger.execute_batch(draft, "ex_retry", **ledger_store)
    before = ledger_store["storage_path"].read_bytes()
    importlib.reload(ledger)
    for _ in range(3):
        assert ledger.execute_batch(draft, "ex_retry", **ledger_store) == first
    assert ledger_store["storage_path"].read_bytes() == before
    assert len(audit.read_events(100, storage_path=ledger_store["audit_path"]).entries) == 1


@pytest.mark.parametrize(
    "changes",
    [{"amount": "101.00"}, {"to_payee_id": "pay_8892", "to_payee_label": "John Smith …8892"}],
)
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_changed_payload_cannot_reuse_committed_identifier(
    changes: dict[str, Any], ledger_store: dict[str, Path]
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_reused", **ledger_store)
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="execution_conflict"):
        ledger.execute_batch(_ledger_draft(_ledger_action(**changes)), "ex_reused", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_fingerprint_binds_transcript_session_and_all_draft_fields(
    ledger_store: dict[str, Path],
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_bound", **ledger_store)
    for changes in (
        {"transcript": "changed"},
        {"session_id": "s_changed"},
        {"draft_id": "d_changed"},
    ):
        with pytest.raises(ledger.LedgerRejected, match="execution_conflict"):
            ledger.execute_batch(_ledger_draft(**changes), "ex_bound", **ledger_store)
    assert _ledger_balance(ledger_store) == "3100.00"


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_thread_race_prevents_double_spending(ledger_store: dict[str, Path]) -> None:
    barrier = Barrier(2)

    def attempt(index: int) -> str:
        barrier.wait(timeout=10)
        try:
            ledger.execute_batch(
                _ledger_draft(_ledger_action(amount="2000.00")), f"ex_race_{index}", **ledger_store
            )
            return "executed"
        except ledger.LedgerRejected as error:
            return error.reason

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert sorted(outcomes) == ["executed", "insufficient_funds"]
    assert _ledger_balance(ledger_store) == "1200.00"
    assert len(_ledger_state(ledger_store)["transactions"]) == 1


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_concurrent_identical_retries_have_one_effect(ledger_store: dict[str, Path]) -> None:
    barrier = Barrier(8)

    def retry(_: int) -> Any:
        barrier.wait(timeout=10)
        return ledger.execute_batch(_ledger_draft(), "ex_once", **ledger_store)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(retry, range(8)))
    assert all(result == results[0] for result in results)
    assert _ledger_balance(ledger_store) == "3100.00"
    assert len(_ledger_state(ledger_store)["transactions"]) == 1


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_process_race_prevents_double_spending(ledger_store: dict[str, Path]) -> None:
    script = (
        _FINANCIAL_KERNEL_PROCESS
        + """
import json, sys
from pathlib import Path
from app.bank import ledger
from app.contracts import Draft
sys.stdin.read(1)
try:
    ledger.execute_batch(Draft.model_validate(json.loads(sys.argv[1])), sys.argv[2],
        storage_path=Path(sys.argv[3]), audit_path=Path(sys.argv[4]))
    sys.stdout.write("executed")
except ledger.LedgerRejected as error:
    sys.stdout.write(error.reason)
"""
    )
    payload = _ledger_draft(_ledger_action(amount="2000.00")).model_dump_json()
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                payload,
                f"ex_process_{index}",
                str(ledger_store["storage_path"]),
                str(ledger_store["audit_path"]),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    try:
        for process in processes:
            process.stdin.write("x")
            process.stdin.flush()
        outcomes = [process.communicate(timeout=20) for process in processes]
        assert all(process.returncode == 0 for process in processes), outcomes
        assert sorted(output for output, _ in outcomes) == ["executed", "insufficient_funds"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert _ledger_balance(ledger_store) == "1200.00"
    assert len(_ledger_state(ledger_store)["transactions"]) == 1


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_audit_failure_leaves_durable_outbox_and_retry_recovers(
    ledger_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append = audit.append_event

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise audit.AuditError("Unavailable")

    monkeypatch.setattr(audit, "append_event", unavailable)
    with pytest.raises(ledger.LedgerAuditPending, match="same execution"):
        ledger.execute_batch(_ledger_draft(), "ex_pending", **ledger_store)
    assert _ledger_balance(ledger_store) == "3100.00"
    assert len(_ledger_state(ledger_store)["outbox"]) == 1
    monkeypatch.setattr(audit, "append_event", append)
    response = ledger.execute_batch(_ledger_draft(), "ex_pending", **ledger_store)
    assert response.results[0].reference == "tx_00000001"
    assert _ledger_balance(ledger_store) == "3100.00"
    assert _ledger_state(ledger_store)["outbox"] == []
    assert audit.verify(storage_path=ledger_store["audit_path"])


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_crash_after_audit_append_reuses_evidence_and_never_redebits(
    ledger_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save = ledger._save_state

    def fail_ack(path: Path, state: dict[str, Any]) -> None:
        if state["executions"] and not state["outbox"]:
            raise readonly.LedgerStorageError("Interrupted acknowledgement")
        save(path, state)

    monkeypatch.setattr(ledger, "_save_state", fail_ack)
    with pytest.raises(readonly.LedgerStorageError):
        ledger.execute_batch(_ledger_draft(), "ex_ack", **ledger_store)
    assert len(_ledger_state(ledger_store)["outbox"]) == 1
    assert len(audit.read_events(100, storage_path=ledger_store["audit_path"]).entries) == 1
    monkeypatch.setattr(ledger, "_save_state", save)
    ledger.reconcile_audit(**ledger_store)
    ledger.execute_batch(_ledger_draft(), "ex_ack", **ledger_store)
    assert _ledger_balance(ledger_store) == "3100.00"
    assert len(audit.read_events(100, storage_path=ledger_store["audit_path"]).entries) == 1
    assert _ledger_state(ledger_store)["outbox"] == []


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_real_process_exit_after_replacement_recovers_once(
    ledger_store: dict[str, Path],
) -> None:
    script = (
        _FINANCIAL_KERNEL_PROCESS
        + """
import json, os, sys
from pathlib import Path
from app.bank import ledger
from app.contracts import Draft
ledger._sync_directory = lambda path: os._exit(73)
ledger.execute_batch(Draft.model_validate(json.loads(sys.argv[1])), "ex_crash",
    storage_path=Path(sys.argv[2]), audit_path=Path(sys.argv[3]))
"""
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            _ledger_draft().model_dump_json(),
            str(ledger_store["storage_path"]),
            str(ledger_store["audit_path"]),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 73, result.stderr
    assert _ledger_balance(ledger_store) == "3100.00"
    assert len(_ledger_state(ledger_store)["outbox"]) == 1
    ledger.execute_batch(_ledger_draft(), "ex_crash", **ledger_store)
    assert _ledger_balance(ledger_store) == "3100.00"
    assert _ledger_state(ledger_store)["outbox"] == []


@pytest.mark.parametrize("failure", ["replace", "fsync"])
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_failed_write_before_replacement_preserves_old_state(
    failure: str,
    ledger_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = ledger_store["storage_path"].read_bytes()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("Fictional disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger.os, failure, fail)
        with pytest.raises(readonly.LedgerStorageError):
            ledger.execute_batch(_ledger_draft(), "ex_disk", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before
    assert not list(ledger_store["storage_path"].parent.glob(".ledger-*"))
    ledger.execute_batch(_ledger_draft(), "ex_disk", **ledger_store)
    assert _ledger_balance(ledger_store) == "3100.00"


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_failed_directory_sync_requires_same_id_retry(
    ledger_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:

        def fail(path: Path) -> None:
            raise OSError("Directory sync failed")

        patch.setattr(ledger, "_sync_directory", fail)
        with pytest.raises(readonly.LedgerStorageError):
            ledger.execute_batch(_ledger_draft(), "ex_sync", **ledger_store)
    assert _ledger_balance(ledger_store) == "3100.00"
    ledger.execute_batch(_ledger_draft(), "ex_sync", **ledger_store)
    assert _ledger_balance(ledger_store) == "3100.00"


@pytest.mark.parametrize("corruption", ["truncated", "checksum", "duplicate", "missing"])
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_corruption_or_missing_established_storage_never_resets(
    corruption: str,
    ledger_store: dict[str, Path],
) -> None:
    path = ledger_store["storage_path"]
    if corruption == "missing":
        path.unlink()
    else:
        original = path.read_text()
        damaged = {
            "truncated": original[:100],
            "checksum": original.replace("3200.00", "9999.00"),
            "duplicate": '{"schema_version":"1.0",' + original[1:],
        }[corruption]
        path.write_text(damaged)
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(readonly.LedgerStorageError):
        ledger.execute_batch(_ledger_draft(), "ex_corrupt", **ledger_store)
    with pytest.raises(readonly.LedgerStorageError):
        readonly.get_ledger_snapshot("u_demo", storage_path=path)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_first_write_failure_is_not_silently_reinitialised(
    ledger_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as patch:

        def fail(*args: Any, **kwargs: Any) -> None:
            raise OSError("Cannot write initial state")

        patch.setattr(ledger.os, "replace", fail)
        with pytest.raises(readonly.LedgerStorageError):
            ledger.execute_batch(_ledger_draft(), "ex_first", **ledger_paths)
    assert not ledger_paths["storage_path"].exists()
    assert ledger_paths["storage_path"].with_suffix(".lock").read_bytes()
    with pytest.raises(readonly.LedgerStorageError):
        ledger.execute_batch(_ledger_draft(), "ex_first", **ledger_paths)


@pytest.mark.parametrize("corruption", ["tamper", "truncated", "deleted"])
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_bad_audit_or_missing_acknowledged_event_blocks_new_money_movements(
    corruption: str,
    ledger_store: dict[str, Path],
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_original", **ledger_store)
    path = ledger_store["audit_path"]
    if corruption == "tamper":
        path.write_text(path.read_text().replace('"count":1', '"count":2'))
    elif corruption == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    else:
        path.unlink()
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.execute_batch(_ledger_draft(), "ex_blocked", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before
    assert _ledger_balance(ledger_store) == "3100.00"


def test_ledger_snapshot_is_detached_and_never_imports_writer(
    ledger_paths: dict[str, Path],
) -> None:
    assert readonly.get_ledger_snapshot("u_demo", storage_path=ledger_paths["storage_path"]) is None
    ledger.reconcile_audit(**ledger_paths)
    snapshot = readonly.get_ledger_snapshot("u_demo", storage_path=ledger_paths["storage_path"])
    assert "executions" not in snapshot and "outbox" not in snapshot
    snapshot["accounts"][0]["balance"] = "0.00"
    assert _ledger_balance(ledger_paths) == "3200.00"
    with pytest.raises(HTTPException) as error:
        readonly.get_ledger_snapshot("u_missing", storage_path=ledger_paths["storage_path"])
    assert error.value.status_code == 404


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_precision_is_independent_of_ambient_decimal_context(
    ledger_store: dict[str, Path],
) -> None:
    with localcontext() as context:
        context.prec = 3
        ledger.execute_batch(
            _ledger_draft(_ledger_action(amount="0.01")), "ex_cent", **ledger_store
        )
    assert _ledger_balance(ledger_store) == "3199.99"


@pytest.mark.parametrize("identifier", ["", "customer-chosen", "ex_", "ex_\nunsafe", None])
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_requires_safe_gateway_execution_identifier(
    identifier: Any, ledger_paths: dict[str, Path]
) -> None:
    with pytest.raises(ledger.LedgerRejected, match="invalid_execution_id"):
        ledger.execute_batch(_ledger_draft(), identifier, **ledger_paths)
    assert not ledger_paths["storage_path"].exists()


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_revalidates_mutated_draft(ledger_store: dict[str, Path]) -> None:
    draft = _ledger_draft()
    draft.actions[0].amount = 1.5
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="invalid_draft"):
        ledger.execute_batch(draft, "ex_float", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_has_no_ai_network_or_public_execution_path(
    ledger_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def network_forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Bank ledger must not contact AI or network services")

    monkeypatch.setattr(socket, "socket", network_forbidden)
    ledger.execute_batch(
        _ledger_draft(transcript="Sensitive fictional instruction 123456"),
        "ex_private",
        **ledger_store,
    )
    assert "Sensitive fictional" not in ledger_store["storage_path"].read_text()
    assert "123456" not in ledger_store["audit_path"].read_text()
    root = Path(__file__).resolve().parents[1]
    for path in (root / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.bank":
                if any(alias.name == "ledger" for alias in node.names):
                    assert path.name == "gateway.py"
            if isinstance(node, ast.ImportFrom) and node.module == "app.bank.ledger":
                assert path.name == "gateway.py"
    # The router delegates to the gateway; it never imports the ledger directly.
    from app.bank import router

    assert "ledger" not in vars(router)


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_fx_audit_contains_documented_fields_and_valid_chain(
    ledger_store: dict[str, Path],
) -> None:
    action = _ledger_action("fx_conversion")
    ledger.execute_batch(_ledger_draft(action), "ex_fx_audit", **ledger_store)
    log = audit.read_events(10, storage_path=ledger_store["audit_path"])
    assert log.chain_valid
    assert [event.event for event in log.entries] == ["transaction_executed", "fx_converted"]
    data = log.entries[1].data
    assert data == {
        "draft_id": "d_ledger_demo",
        "action_index": 0,
        "transaction_id": log.entries[0].data["transaction_id"],
        **{
            field: action[field]
            for field in ("sell_amount", "sell_currency", "buy_amount", "buy_currency", "rate")
        },
    }
    assert _ledger_state(ledger_store)["outbox"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("sell_amount", "1.001"),
        ("sell_amount", 1.00),
        ("buy_amount", "-1.00"),
        ("buy_amount", "NaN"),
        ("buy_amount", {"transcript": "secret"}),
        ("sell_currency", "sgd"),
        ("buy_currency", "US"),
        ("buy_currency", "USD-secret"),
        ("rate", "0.000000"),
        ("rate", "-1.000000"),
        ("rate", "0.7400001"),
        ("rate", 0.740000),
        ("rate", "Infinity"),
        ("rate", "0.740000;private-key"),
    ],
)
def test_audit_fx_allowlist_remains_strict(
    field: str, value: Any, ledger_paths: dict[str, Path]
) -> None:
    with pytest.raises(audit.AuditError):
        audit.append_event(
            "bank", "fx_converted", {field: value}, storage_path=ledger_paths["audit_path"]
        )
    assert not ledger_paths["audit_path"].exists()


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_partial_fx_audit_delivery_recovers_every_event(
    ledger_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append = audit.append_event

    def fail_fx(actor: str, event: str, data: dict[str, Any], **kwargs: Any) -> Any:
        if event == "fx_converted":
            raise audit.AuditError("FX audit interrupted")
        return append(actor, event, data, **kwargs)

    monkeypatch.setattr(audit, "append_event", fail_fx)
    draft = _ledger_draft(_ledger_action("fx_conversion"))
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.execute_batch(draft, "ex_fx_pending", **ledger_store)
    assert _ledger_balance(ledger_store, "acc_5678") == "64000.00"
    assert len(_ledger_state(ledger_store)["outbox"]) == 2
    monkeypatch.setattr(audit, "append_event", append)
    ledger.execute_batch(draft, "ex_fx_pending", **ledger_store)
    assert _ledger_balance(ledger_store, "acc_5678") == "64000.00"
    assert _ledger_balance(ledger_store, "acc_7788") == "30740.00"
    assert _ledger_state(ledger_store)["outbox"] == []
    log = audit.read_events(10, storage_path=ledger_store["audit_path"])
    assert log.chain_valid
    assert [entry.event for entry in log.entries] == [
        "transaction_executed",
        "fx_converted",
    ]


@pytest.mark.parametrize(
    "corruption",
    [
        "negative_balance",
        "float_balance",
        "duplicate_accounts",
        "duplicate_holdings",
        "foreign_holdings",
        "bad_quantity",
        "bill_state",
        "execution_record",
        "outbox",
    ],
)
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_structural_corruption_fails_even_with_recomputed_checksum(
    corruption: str,
    ledger_store: dict[str, Path],
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_valid", **ledger_store)
    state = _ledger_state(ledger_store)
    user = state["users"]["u_demo"]
    if corruption == "negative_balance":
        user["accounts"][0]["balance"] = "-1.00"
    elif corruption == "float_balance":
        user["accounts"][0]["balance"] = 3100.00
    elif corruption == "duplicate_accounts":
        user["accounts"].append(user["accounts"][0])
    elif corruption == "duplicate_holdings":
        user["holdings"].append(user["holdings"][0])
    elif corruption == "foreign_holdings":
        user["holdings"][0]["account_id"] = "acc_foreign"
    elif corruption == "bad_quantity":
        user["holdings"][0]["quantity"] = True
    elif corruption == "bill_state":
        user["billers"][0]["payment_state"] = "paid"
    elif corruption == "execution_record":
        state["executions"]["ex_valid"]["response"]["results"][0]["reference"] = "tx_fake"
    else:
        state["outbox"] = [
            {"event": "transaction_executed", "data": {"transaction_id": "tx_forged", "count": 1}}
        ]
    state["checksum"] = readonly._ledger_digest(state)
    ledger_store["storage_path"].write_text(json.dumps(state) + "\n")
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(readonly.LedgerStorageError):
        ledger.execute_batch(_ledger_draft(), "ex_blocked", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.parametrize(
    "kind,reason",
    [("account", "account_inactive"), ("payee", "recipient_inactive"), ("bill", "biller_inactive")],
)
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_inactive_records_block_payments(
    kind: str,
    reason: str,
    ledger_paths: dict[str, Path],
    bank_seed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = {"account": "accounts", "payee": "payees", "bill": "billers"}[kind]
    bank_seed["users"]["u_demo"][collection][0]["status"] = "inactive"
    monkeypatch.setattr(readonly, "_load_database", lambda: bank_seed)
    ledger.reconcile_audit(**ledger_paths)
    before = ledger_paths["storage_path"].read_bytes()
    action = _ledger_action("bill_payment" if kind == "bill" else "transfer")
    with pytest.raises(ledger.LedgerRejected, match=reason):
        ledger.execute_batch(_ledger_draft(action), "ex_inactive", **ledger_paths)
    assert ledger_paths["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_missing_seed_fails_without_creating_fake_balances(
    ledger_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> dict[str, Any]:
        raise FileNotFoundError("Seed missing")

    monkeypatch.setattr(readonly, "_load_database", missing)
    with pytest.raises(readonly.LedgerStorageError):
        ledger.execute_batch(_ledger_draft(), "ex_missing_seed", **ledger_paths)
    assert not ledger_paths["storage_path"].exists()


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_distinct_concurrent_requests_preserve_all_updates(
    ledger_store: dict[str, Path],
) -> None:
    def debit(index: int) -> Any:
        return ledger.execute_batch(
            _ledger_draft(_ledger_action(amount="0.01")), f"ex_stress_{index}", **ledger_store
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(debit, range(30)))
    assert len({response.results[0].reference for response in responses}) == 30
    assert _ledger_balance(ledger_store) == "3199.70"
    assert len(_ledger_state(ledger_store)["transactions"]) == 30
    assert audit.verify(storage_path=ledger_store["audit_path"])


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_default_path_is_independent_of_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_loader = readonly._load_database
    monkeypatch.setattr(readonly, "_load_database", original_loader)
    # Capture actual seed before changing __file__, which also owns its seed path.
    database = original_loader()
    monkeypatch.setattr(readonly, "_load_database", lambda: database)
    monkeypatch.setattr(readonly, "__file__", str(tmp_path / "app" / "bank" / "readonly.py"))
    monkeypatch.chdir(tmp_path.parent)
    ledger.execute_batch(_ledger_draft(), "ex_path", audit_path=tmp_path / "audit.jsonl")
    assert (tmp_path / "var" / "ledger.json").exists()
    assert readonly.get_ledger_snapshot("u_demo")["accounts"][0]["balance"] == "3100.00"


def test_forecast_rejects_snapshot_from_other_user(ledger_store: dict[str, Path]) -> None:
    snapshot = readonly.get_ledger_snapshot("u_demo", storage_path=ledger_store["storage_path"])
    snapshot["user_id"] = "u_other"
    with pytest.raises(readonly.ForecastDataError):
        readonly.get_cashflow_forecast(
            "u_demo",
            "acc_1234",
            runtime_snapshot=snapshot,
            as_of=datetime.fromisoformat("2026-10-13T00:00:00+08:00"),
            horizon_days=30,
            proposed_amount=Decimal("0.00"),
        )


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_missing_state_and_marker_cannot_reset_when_execution_audits_exist(
    ledger_store: dict[str, Path],
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_before_loss", **ledger_store)
    ledger_store["storage_path"].unlink()
    ledger_store["storage_path"].with_suffix(".lock").unlink()
    with pytest.raises(readonly.LedgerStorageError, match="ledger state is missing"):
        ledger.execute_batch(_ledger_draft(), "ex_after_loss", **ledger_store)
    assert not ledger_store["storage_path"].exists()


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_invalid_audit_prevents_first_initialisation(ledger_paths: dict[str, Path]) -> None:
    ledger_paths["audit_path"].write_text("incomplete")
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.execute_batch(_ledger_draft(), "ex_unsafe_init", **ledger_paths)
    assert not ledger_paths["storage_path"].exists()


@pytest.mark.parametrize("kind", ["transfer", "bill_payment", "stock_order"])
@pytest.mark.usefixtures("financial_kernel")
def test_ledger_enforces_documented_unique_customer_action_types(
    kind: str,
    ledger_store: dict[str, Path],
) -> None:
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="invalid_action_count"):
        ledger.execute_batch(
            _ledger_draft(_ledger_action(kind), _ledger_action(kind)),
            "ex_duplicates",
            **ledger_store,
        )
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_ledger_unaffordable_stock_order_rolls_back_earlier_bill_payment(
    ledger_store: dict[str, Path],
) -> None:
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="insufficient_funds"):
        ledger.execute_batch(
            _ledger_draft(
                _ledger_action("bill_payment"), _ledger_action("stock_order", quantity=132)
            ),
            "ex_bill_and_stock",
            **ledger_store,
        )
    assert ledger_store["storage_path"].read_bytes() == before
    assert _ledger_balance(ledger_store) == "3200.00"


@pytest.fixture
def integrated_views(
    ledger_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    """Point real account/forecast readers at the same isolated execution store."""
    monkeypatch.setattr(config, "BANK_EXECUTION_ENABLED", True)
    monkeypatch.setattr(readonly, "_ledger_path", lambda path: path or ledger_paths["storage_path"])
    return ledger_paths


def _runtime_forecast() -> dict[str, Any]:
    return readonly.get_cashflow_forecast(
        "u_demo",
        "acc_1234",
        as_of=datetime.fromisoformat("2026-10-13T00:00:00+08:00"),
        horizon_days=30,
        proposed_amount=Decimal("0.00"),
    )


@pytest.mark.usefixtures("financial_kernel")
def test_integration_transfer_updates_account_api_and_default_forecast(
    integrated_views: dict[str, Path],
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_api_transfer", **integrated_views)
    response = TestClient(app).get("/api/accounts?user_id=u_demo")
    assert response.status_code == 200
    result = response.json()
    assert result["accounts"][0]["balance"] == "3100.00"
    assert set(result) == {"accounts", "payees", "billers", "holdings"}
    assert set(result["accounts"][0]) == {"account_id", "label", "type", "currency", "balance"}
    assert set(result["holdings"][0]) == {"ticker", "quantity"}
    assert set(result["billers"][0]) == {"biller_id", "name", "label", "amount_due"}
    assert set(result["payees"][0]) == {"payee_id", "name", "label"}
    assert isinstance(readonly.get_accounts("u_demo").accounts[0].balance, Decimal)
    forecast = _runtime_forecast()
    assert forecast["current_balance"] == "3100.00"
    assert forecast["commitment_total"] == "2782.40"
    assert forecast["projected_balance_after"] == "317.60"


@pytest.mark.usefixtures("financial_kernel")
def test_integration_stock_orders_update_public_holdings(integrated_views: dict[str, Path]) -> None:
    ledger.execute_batch(
        _ledger_draft(_ledger_action("stock_order")), "ex_api_stock", **integrated_views
    )
    data = TestClient(app).get("/api/accounts?user_id=u_demo").json()
    assert (
        next(a["balance"] for a in data["accounts"] if a["account_id"] == "acc_7788") == "7250.00"
    )
    assert next(h["quantity"] for h in data["holdings"] if h["ticker"] == "AAPL") == 110
    ledger.execute_batch(
        _ledger_draft(_ledger_action("stock_order", side="sell", quantity=1)),
        "ex_api_sell",
        **integrated_views,
    )
    data = TestClient(app).get("/api/accounts?user_id=u_demo").json()
    assert next(h["quantity"] for h in data["holdings"] if h["ticker"] == "AAPL") == 109


@pytest.mark.usefixtures("financial_kernel")
def test_integration_partial_and_settled_bill_do_not_double_count(
    integrated_views: dict[str, Path],
) -> None:
    ledger.reconcile_audit(**integrated_views)
    before = _runtime_forecast()
    assert before["projected_balance_after"] == "417.60"
    ledger.execute_batch(
        _ledger_draft(_ledger_action("bill_payment", amount="240.00")),
        "ex_api_partial",
        **integrated_views,
    )
    partial = _runtime_forecast()
    assert partial["current_balance"] == "2960.00"
    assert partial["commitment_total"] == "2542.40"
    assert partial["projected_balance_after"] == before["projected_balance_after"]
    ledger.execute_batch(
        _ledger_draft(_ledger_action("bill_payment", amount="1000.00")),
        "ex_api_paid",
        **integrated_views,
    )
    settled = _runtime_forecast()
    assert settled["current_balance"] == "1960.00"
    assert settled["commitment_total"] == "1542.40"
    assert settled["projected_balance_after"] == before["projected_balance_after"]
    assert {item["amount"] for item in settled["commitments"]} == {"1450.00", "92.40"}
    data = TestClient(app).get("/api/accounts?user_id=u_demo").json()
    assert data["accounts"][0]["balance"] == "1960.00"
    assert data["billers"][0]["amount_due"] == "0.00"


@pytest.mark.usefixtures("financial_kernel")
def test_integration_rejected_batch_leaves_displayed_views_unchanged(
    integrated_views: dict[str, Path],
) -> None:
    ledger.reconcile_audit(**integrated_views)
    client = TestClient(app)
    before_accounts = client.get("/api/accounts?user_id=u_demo").json()
    before_forecast = _runtime_forecast()
    with pytest.raises(ledger.LedgerRejected, match="insufficient_funds"):
        ledger.execute_batch(
            _ledger_draft(
                _ledger_action("bill_payment"), _ledger_action("stock_order", quantity=132)
            ),
            "ex_api_reject",
            **integrated_views,
        )
    assert client.get("/api/accounts?user_id=u_demo").json() == before_accounts
    assert _runtime_forecast() == before_forecast


@pytest.mark.usefixtures("financial_kernel")
def test_integration_crash_retry_reuses_audit_evidence_and_committed_views(
    integrated_views: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger.reconcile_audit(**integrated_views)
    save = ledger._save_state

    def interrupted_ack(path: Path, state: dict[str, Any]) -> None:
        if state["executions"] and not state["outbox"]:
            raise readonly.LedgerStorageError("Simulated acknowledgement crash")
        save(path, state)

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_save_state", interrupted_ack)
        with pytest.raises(readonly.LedgerStorageError):
            ledger.execute_batch(_ledger_draft(), "ex_api_crash", **integrated_views)
    original_evidence = integrated_views["audit_path"].read_bytes()
    client = TestClient(app)
    assert client.get("/api/accounts?user_id=u_demo").json()["accounts"][0]["balance"] == "3100.00"
    pending = _ledger_state(integrated_views)
    assert len(pending["outbox"]) == 1
    original_forecast = _runtime_forecast()
    for _ in range(3):
        ledger.execute_batch(_ledger_draft(), "ex_api_crash", **integrated_views)
        ledger.reconcile_audit(**integrated_views)
    assert integrated_views["audit_path"].read_bytes() == original_evidence
    assert _ledger_state(integrated_views)["outbox"] == []
    assert len(_ledger_state(integrated_views)["transactions"]) == 1
    assert _runtime_forecast() == original_forecast
    assert audit.verify(storage_path=integrated_views["audit_path"])


@pytest.mark.parametrize(
    "failure", ["missing", "truncated", "checksum", "lost_marker_too", "lost_all_evidence"]
)
@pytest.mark.usefixtures("financial_kernel")
def test_integration_missing_or_corrupt_state_never_displays_seed_balances(
    failure: str,
    integrated_views: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_existing", **integrated_views)
    path = integrated_views["storage_path"]
    if failure in {"missing", "lost_marker_too", "lost_all_evidence"}:
        path.unlink()
        if failure != "missing":
            path.with_suffix(".lock").unlink()
        if failure == "lost_all_evidence":
            integrated_views["audit_path"].unlink()
            integrated_views["audit_path"].with_suffix(".lock").unlink()
    elif failure == "truncated":
        path.write_text("{")
    else:
        path.write_text(path.read_text().replace("3100.00", "9999.00"))
    before = {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()}

    def no_seed() -> dict[str, Any]:
        raise AssertionError("Must not substitute seed for missing runtime evidence")

    monkeypatch.setattr(readonly, "_load_database", no_seed)
    response = TestClient(app).get("/api/accounts?user_id=u_demo")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "upstream_error",
            "message": "Banking state unavailable; operator review required.",
        }
    }
    with pytest.raises(HTTPException) as error:
        _runtime_forecast()
    assert error.value.status_code == 503
    assert {p.name: p.read_bytes() for p in path.parent.iterdir() if p.is_file()} == before


def test_integration_ambiguous_first_use_requires_supervised_initialisation(
    integrated_views: dict[str, Path],
) -> None:
    before = set(integrated_views["storage_path"].parent.iterdir())
    response = TestClient(app).get("/api/accounts?user_id=u_demo")
    assert response.status_code == 503
    with pytest.raises(HTTPException) as error:
        _runtime_forecast()
    assert error.value.status_code == 503
    assert {
        p for p in integrated_views["storage_path"].parent.iterdir() if p.name != "evidence"
    } == before
    ledger.reconcile_audit(**integrated_views)
    assert (
        TestClient(app).get("/api/accounts?user_id=u_demo").json()["accounts"][0]["balance"]
        == "3200.00"
    )


@pytest.mark.usefixtures("financial_kernel")
def test_integration_reads_never_write_reconcile_execute_or_contact_ai(
    integrated_views: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_read_only", **integrated_views)
    before = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in integrated_views["storage_path"].parent.iterdir()
        if p.is_file()
    }

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Read must not execute, initialise, reconcile, write or contact AI")

    monkeypatch.setattr(ledger, "execute_batch", forbidden)
    monkeypatch.setattr(ledger, "reconcile_audit", forbidden)
    monkeypatch.setattr(ledger, "_load_state", forbidden)
    monkeypatch.setattr(audit, "append_event", forbidden)
    monkeypatch.setattr(audit, "read_events", forbidden)
    monkeypatch.setattr(ledger.os, "replace", forbidden)
    monkeypatch.setattr(ledger.os, "fsync", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(readonly, "_load_database", forbidden)
    assert readonly.get_accounts("u_demo").accounts[0].balance == Decimal("3100.00")
    assert _runtime_forecast()["current_balance"] == "3100.00"
    assert {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in integrated_views["storage_path"].parent.iterdir()
        if p.is_file()
    } == before


def test_integration_views_read_one_snapshot_without_mixing_revisions(
    integrated_views: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger.reconcile_audit(**integrated_views)
    read = readonly.get_ledger_snapshot
    calls = []

    def snapshot(user_id: str) -> dict[str, Any] | None:
        calls.append(user_id)
        return read(user_id)

    monkeypatch.setattr(readonly, "get_ledger_snapshot", snapshot)
    readonly.get_accounts("u_demo")
    assert calls == ["u_demo"]
    calls.clear()
    _runtime_forecast()
    assert calls == ["u_demo"]


def test_integration_runtime_views_do_not_require_seed_and_preserve_unknown_user_404(
    integrated_views: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger.reconcile_audit(**integrated_views)

    def missing_seed() -> dict[str, Any]:
        raise FileNotFoundError("Removed seed after initialisation")

    monkeypatch.setattr(readonly, "_load_database", missing_seed)
    client = TestClient(app)
    assert client.get("/api/accounts?user_id=u_demo").status_code == 200
    assert _runtime_forecast()["current_balance"] == "3200.00"
    assert client.get("/api/accounts?user_id=u_unknown").status_code == 404


def test_audit_idempotent_append_reuses_complete_original_evidence(
    ledger_paths: dict[str, Path],
) -> None:
    path = ledger_paths["audit_path"]
    data = {"transaction_id": "tx_stable", "count": 1, "reason": "completed"}
    first = audit.append_event(
        "bank", "transaction_executed", data, storage_path=path, idempotent=True
    )
    before = path.read_bytes()
    retry = audit.append_event(
        "bank",
        "transaction_executed",
        dict(reversed(list(data.items()))),
        storage_path=path,
        idempotent=True,
    )
    assert retry == first
    assert path.read_bytes() == before
    assert audit.verify(storage_path=path)


@pytest.mark.parametrize(
    "change", [{"count": 2}, {"reason": "changed"}, {"account_id": "acc_extra"}]
)
def test_audit_idempotent_append_rejects_conflicting_complete_payloads(
    change: dict[str, Any], ledger_paths: dict[str, Path]
) -> None:
    path = ledger_paths["audit_path"]
    data = {"transaction_id": "tx_conflict", "count": 1, "reason": "completed"}
    audit.append_event("bank", "transaction_executed", data, storage_path=path)
    before = path.read_bytes()
    with pytest.raises(audit.AuditError, match="conflicts"):
        audit.append_event(
            "bank", "transaction_executed", {**data, **change}, storage_path=path, idempotent=True
        )
    assert path.read_bytes() == before


def test_audit_opt_in_keeps_default_appends_and_existing_duplicate_records(
    ledger_paths: dict[str, Path],
) -> None:
    path = ledger_paths["audit_path"]
    data = {"transaction_id": "tx_legacy", "count": 1}
    original = audit.append_event("bank", "transaction_executed", data, storage_path=path)
    audit.append_event("bank", "transaction_executed", data, storage_path=path)
    before = path.read_bytes()
    assert (
        audit.append_event("bank", "transaction_executed", data, storage_path=path, idempotent=True)
        == original
    )
    assert path.read_bytes() == before
    assert len(audit.read_events(10, storage_path=path).entries) == 2


def test_audit_conflicting_legacy_duplicates_are_not_silently_reused(
    ledger_paths: dict[str, Path],
) -> None:
    path = ledger_paths["audit_path"]
    data = {"transaction_id": "tx_conflicting_legacy", "count": 1}
    audit.append_event("bank", "transaction_executed", data, storage_path=path)
    audit.append_event("bank", "transaction_executed", {**data, "count": 2}, storage_path=path)
    before = path.read_bytes()
    with pytest.raises(audit.AuditError, match="conflicts"):
        audit.append_event("bank", "transaction_executed", data, storage_path=path, idempotent=True)
    assert path.read_bytes() == before


def test_audit_event_identity_separates_actor_type_and_action_index(
    ledger_paths: dict[str, Path],
) -> None:
    path = ledger_paths["audit_path"]
    entries = [
        audit.append_event(
            actor,
            event,
            {"transaction_id": "tx_identity", "action_index": index},
            storage_path=path,
            idempotent=True,
        )
        for actor, event, index in (
            ("bank", "fx_converted", 0),
            ("bank", "fx_converted", 1),
            ("bank", "transaction_executed", 0),
            ("gateway", "fx_converted", 0),
        )
    ]
    assert len({entry.hash for entry in entries}) == 4


def test_audit_concurrent_idempotent_thread_retries_add_one_record(
    ledger_paths: dict[str, Path],
) -> None:
    path = ledger_paths["audit_path"]
    barrier = Barrier(8)

    def retry(_: int) -> Any:
        barrier.wait(timeout=10)
        return audit.append_event(
            "bank",
            "transaction_executed",
            {"transaction_id": "tx_threads", "count": 1},
            storage_path=path,
            idempotent=True,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(retry, range(8)))
    assert all(record == records[0] for record in records)
    assert len(audit.read_events(10, storage_path=path).entries) == 1


def test_audit_concurrent_idempotent_process_retries_add_one_record(
    ledger_paths: dict[str, Path],
) -> None:
    script = """
import sys
from pathlib import Path
from app import audit
sys.stdin.read(1)
entry = audit.append_event("bank", "transaction_executed",
    {"transaction_id": "tx_process_retry", "count": 1},
    storage_path=Path(sys.argv[1]), idempotent=True)
sys.stdout.write(entry.hash)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(ledger_paths["audit_path"])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    try:
        for process in processes:
            process.stdin.write("x")
            process.stdin.flush()
        outputs = [process.communicate(timeout=20) for process in processes]
        assert all(process.returncode == 0 for process in processes), outputs
        assert len({output for output, _ in outputs}) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert len(audit.read_events(10, storage_path=ledger_paths["audit_path"]).entries) == 1


def test_audit_idempotent_retry_checks_chain_before_reusing_evidence(
    ledger_paths: dict[str, Path],
) -> None:
    path = ledger_paths["audit_path"]
    data = {"transaction_id": "tx_tamper", "count": 1}
    audit.append_event("bank", "transaction_executed", data, storage_path=path)
    path.write_text(path.read_text().replace('"count":1', '"count":2'))
    before = path.read_bytes()
    with pytest.raises(audit.AuditError, match="chain verification"):
        audit.append_event("bank", "transaction_executed", data, storage_path=path, idempotent=True)
    assert path.read_bytes() == before


def test_audit_idempotent_retry_resynchronises_after_interrupted_fsync(
    ledger_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = ledger_paths["audit_path"]
    data = {"transaction_id": "tx_sync_retry", "count": 1}
    with monkeypatch.context() as patch:

        def fail(descriptor: int) -> None:
            raise OSError("Interrupted synchronisation")

        patch.setattr(audit.os, "fsync", fail)
        with pytest.raises(audit.AuditError):
            audit.append_event(
                "bank", "transaction_executed", data, storage_path=path, idempotent=True
            )
    before = path.read_bytes()
    sync = audit.os.fsync
    calls = []

    def synchronise(descriptor: int) -> None:
        calls.append(descriptor)
        sync(descriptor)

    monkeypatch.setattr(audit.os, "fsync", synchronise)
    entry = audit.append_event(
        "bank", "transaction_executed", data, storage_path=path, idempotent=True
    )
    assert entry.index == 0
    assert len(calls) == (1 if audit.os.name == "nt" else 2)
    assert path.read_bytes() == before


def test_audit_idempotency_requires_explicit_safe_identity(ledger_paths: dict[str, Path]) -> None:
    with pytest.raises(audit.AuditError, match="transaction identity"):
        audit.append_event(
            "bank",
            "transaction_executed",
            {"count": 1},
            storage_path=ledger_paths["audit_path"],
            idempotent=True,
        )
    assert ledger_paths["audit_path"].read_bytes() == b""


@pytest.mark.usefixtures("financial_kernel")
def test_integration_conflicting_pending_audit_refuses_acknowledgement(
    integrated_views: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    append = audit.append_event

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise audit.AuditError("Temporarily unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(audit, "append_event", unavailable)
        with pytest.raises(ledger.LedgerAuditPending):
            ledger.execute_batch(_ledger_draft(), "ex_conflicting_evidence", **integrated_views)
    state = _ledger_state(integrated_views)
    event = state["outbox"][0]
    append(
        "bank",
        event["event"],
        {**event["data"], "count": 2},
        storage_path=integrated_views["audit_path"],
    )
    before = integrated_views["storage_path"].read_bytes()
    audit_before = integrated_views["audit_path"].read_bytes()
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.execute_batch(_ledger_draft(), "ex_conflicting_evidence", **integrated_views)
    assert integrated_views["storage_path"].read_bytes() == before
    assert integrated_views["audit_path"].read_bytes() == audit_before
    assert readonly.get_accounts("u_demo").accounts[0].balance == Decimal("3100.00")


@pytest.mark.usefixtures("financial_kernel")
def test_integration_real_crash_after_audit_append_reuses_exact_evidence(
    integrated_views: dict[str, Path],
) -> None:
    ledger.reconcile_audit(**integrated_views)
    script = (
        _FINANCIAL_KERNEL_PROCESS
        + """
import json, os, sys
from pathlib import Path
from app.bank import ledger
from app.contracts import Draft
save = ledger._save_state
def interrupted_ack(path, state):
    if state["executions"] and not state["outbox"]:
        os._exit(74)
    save(path, state)
ledger._save_state = interrupted_ack
ledger.execute_batch(Draft.model_validate(json.loads(sys.argv[1])), "ex_real_audit_crash",
    storage_path=Path(sys.argv[2]), audit_path=Path(sys.argv[3]))
"""
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            _ledger_draft().model_dump_json(),
            str(integrated_views["storage_path"]),
            str(integrated_views["audit_path"]),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 74, result.stderr
    original_evidence = integrated_views["audit_path"].read_bytes()
    assert readonly.get_accounts("u_demo").accounts[0].balance == Decimal("3100.00")
    assert len(_ledger_state(integrated_views)["outbox"]) == 1
    ledger.execute_batch(_ledger_draft(), "ex_real_audit_crash", **integrated_views)
    assert integrated_views["audit_path"].read_bytes() == original_evidence
    assert _ledger_state(integrated_views)["outbox"] == []
    assert readonly.get_accounts("u_demo").accounts[0].balance == Decimal("3100.00")


@pytest.mark.usefixtures("financial_kernel")
def test_integration_conflicting_acknowledged_audit_blocks_new_execution(
    integrated_views: dict[str, Path],
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_acknowledged", **integrated_views)
    event = _ledger_state(integrated_views)["executions"]["ex_acknowledged"]["audit_events"][0]
    audit.append_event(
        "bank",
        event["event"],
        {**event["data"], "count": 2},
        storage_path=integrated_views["audit_path"],
    )
    before = integrated_views["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.execute_batch(_ledger_draft(), "ex_new_blocked", **integrated_views)
    assert integrated_views["storage_path"].read_bytes() == before


def test_integration_permission_failure_is_sanitised_without_seed_fallback(
    integrated_views: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exists = Path.exists

    def denied(path: Path) -> bool:
        if path == integrated_views["storage_path"]:
            raise PermissionError("Sensitive storage path details")
        return exists(path)

    monkeypatch.setattr(Path, "exists", denied)
    with pytest.raises(HTTPException) as error:
        readonly.get_accounts("u_demo")
    assert error.value.status_code == 503
    assert "Sensitive" not in error.value.detail


def test_integration_invalid_runtime_schedule_is_not_zero_expenses(
    integrated_views: dict[str, Path],
) -> None:
    ledger.reconcile_audit(**integrated_views)
    state = _ledger_state(integrated_views)
    state["users"]["u_demo"].pop("cashflow_schedule")
    state["checksum"] = readonly._ledger_digest(state)
    integrated_views["storage_path"].write_text(json.dumps(state) + "\n")
    with pytest.raises(readonly.ForecastDataError, match="unavailable"):
        _runtime_forecast()


@pytest.fixture
def risk_policy() -> rules.TransferPolicy:
    """Proposed fictional values for tests only; these configure no live flow."""
    return rules.TransferPolicy(
        currency="SGD",
        per_transfer_max="20000.00",
        daily_transfer_max="20000.00",
        step_up_above="5000.00",
        velocity_max_transfers=5,
        velocity_window_minutes=60,
        daily_timezone="Asia/Singapore",
    )


@pytest.fixture
def risk_state(ledger_store: dict[str, Path]) -> dict[str, Any]:
    """A detached, explicitly initialised J-08 snapshot; no evaluator writes occur."""
    return _ledger_state(ledger_store)


@pytest.fixture
def risk_now() -> datetime:
    return datetime.fromisoformat("2026-10-14T12:00:00+08:00")


def _risk_draft(amount: str = "100.00", **changes: Any) -> Draft:
    return _ledger_draft(
        _ledger_action(
            **{
                "amount": amount,
                "from_account_id": "acc_5678",
                "from_account_label": "Current …5678",
                **changes,
            },
        )
    )


def _risk_history(
    state: dict[str, Any],
    *,
    amount: str,
    completed_at: datetime,
    account_id: str = "acc_5678",
    user_id: str = "u_demo",
    execution_id: str | None = None,
    draft: Draft | None = None,
) -> Draft:
    """Build committed test evidence in the actual J-08 record/cache format."""
    user = state["users"][user_id]
    account = next(a for a in user["accounts"] if a["account_id"] == account_id)
    draft = draft or _ledger_draft(
        _ledger_action(
            from_account_id=account_id,
            from_account_label=account["label"],
            amount=amount,
            currency=account["currency"],
        ),
        user_id=user_id,
    )
    reference = f"tx_{len(state['transactions']) + 1:08d}"
    identifier = execution_id or f"ex_history_{len(state['executions']) + 1}"
    payload = json.dumps(
        draft.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    event = {
        "event": "transaction_executed",
        "data": {
            "transaction_id": "tx_" + hashlib.sha256(identifier.encode()).hexdigest(),
            "count": 1,
        },
    }
    state["transactions"].append(
        {
            "reference": reference,
            "execution_id": identifier,
            "user_id": user_id,
            "action_index": 0,
            "completed_at": completed_at.isoformat(),
            "action": draft.actions[0].model_dump(mode="json"),
            "execution_details": {},
        }
    )
    state["executions"][identifier] = {
        "fingerprint": hashlib.sha256(payload.encode()).hexdigest(),
        "audit_events": [event],
        "response": {
            "status": "executed",
            "results": [{"action_index": 0, "reference": reference}],
        },
    }
    state["next_reference"] += 1
    return draft


def _risk_evaluate(
    policy: rules.TransferPolicy | None,
    state: dict[str, Any],
    now: datetime,
    amount: str = "100.00",
    *,
    draft: Draft | None = None,
    execution_id: str = "ex_candidate",
) -> rules.RiskDecision:
    return rules.evaluate_risk(
        draft or _risk_draft(amount), policy=policy, state=state, now=now, execution_id=execution_id
    )


@pytest.mark.parametrize(
    "amount,status,reason",
    [
        ("0.00", "reject", "invalid_amount"),
        ("0.01", "allow", "allowed"),
        ("4999.99", "allow", "allowed"),
        ("5000.00", "allow", "allowed"),
        ("5000.01", "needs_extra_check", "step_up_required"),
        ("8000.00", "needs_extra_check", "step_up_required"),
        ("20000.00", "needs_extra_check", "step_up_required"),
        ("20000.01", "reject", "per_transfer_limit_exceeded"),
        ("50000.00", "reject", "per_transfer_limit_exceeded"),
    ],
)
def test_risk_transfer_and_step_up_threshold_boundaries(
    amount: str,
    status: str,
    reason: str,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    assert _risk_evaluate(risk_policy, risk_state, risk_now, amount).model_dump() == {
        "status": status,
        "reason": reason,
        "method": "otp",
    }


@pytest.mark.parametrize(
    "field",
    [
        "currency",
        "per_transfer_max",
        "daily_transfer_max",
        "step_up_above",
        "velocity_max_transfers",
        "velocity_window_minutes",
        "daily_timezone",
    ],
)
def test_risk_policy_requires_every_field(field: str, risk_policy: rules.TransferPolicy) -> None:
    data = risk_policy.model_dump(mode="python")
    data.pop(field)
    with pytest.raises(ValidationError):
        rules.TransferPolicy.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("per_transfer_max", 20000.00),
        ("per_transfer_max", "0.00"),
        ("daily_transfer_max", "-1.00"),
        ("step_up_above", "NaN"),
        ("step_up_above", "Infinity"),
        ("step_up_above", "5000.001"),
        ("step_up_above", "20000.00"),
        ("daily_transfer_max", "19999.99"),
        ("velocity_max_transfers", True),
        ("velocity_max_transfers", "5"),
        ("velocity_max_transfers", 0),
        ("velocity_window_minutes", 60.0),
        ("velocity_window_minutes", -1),
        ("velocity_window_minutes", 10**30),
        ("currency", "USD"),
        ("daily_timezone", "UTC"),
        ("verified", True),
    ],
)
def test_risk_policy_rejects_invalid_configuration(
    field: str,
    value: Any,
    risk_policy: rules.TransferPolicy,
) -> None:
    with pytest.raises(ValidationError):
        rules.TransferPolicy.model_validate({**risk_policy.model_dump(mode="python"), field: value})


def test_risk_missing_or_forged_policy_fails_closed(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    assert _risk_evaluate(None, risk_state, risk_now).reason == "policy_missing"
    for policy in (
        {},
        False,
        risk_policy.model_copy(update={"per_transfer_max": Decimal("-1.00")}),
        risk_policy.model_copy(update={"velocity_max_transfers": True}),
    ):
        result = _risk_evaluate(policy, risk_state, risk_now)
        assert result.status == "reject" and result.reason == "policy_invalid"


@pytest.mark.parametrize("amount,status", [("100.00", "allow"), ("100.01", "reject")])
def test_risk_daily_totals_cross_accounts_with_exact_cent_boundary(
    amount: str,
    status: str,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    _risk_history(
        risk_state,
        amount="3000.00",
        account_id="acc_1234",
        completed_at=risk_now - timedelta(hours=3),
    )
    _risk_history(risk_state, amount="16900.00", completed_at=risk_now - timedelta(hours=2))
    result = _risk_evaluate(risk_policy, risk_state, risk_now, amount)
    assert result.status == status
    assert result.reason == ("allowed" if status == "allow" else "daily_limit_exceeded")


def test_risk_daily_hard_limit_precedes_step_up(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    _risk_history(risk_state, amount="15000.00", completed_at=risk_now - timedelta(hours=2))
    assert (
        _risk_evaluate(risk_policy, risk_state, risk_now, "8000.00").reason
        == "daily_limit_exceeded"
    )


@pytest.mark.parametrize("count,status", [(4, "allow"), (5, "reject"), (6, "reject")])
def test_risk_velocity_counts_successful_transfers_per_customer(
    count: int,
    status: str,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    for index in range(count):
        _risk_history(
            risk_state,
            amount="1.00",
            account_id="acc_1234" if index % 2 else "acc_5678",
            completed_at=risk_now - timedelta(minutes=index),
        )
    decision = _risk_evaluate(risk_policy, risk_state, risk_now)
    assert decision.status == status
    assert decision.reason == ("allowed" if status == "allow" else "velocity_exceeded")


@pytest.mark.parametrize(
    "age,status",
    [
        (timedelta(minutes=60), "allow"),
        (timedelta(minutes=60) - timedelta(microseconds=1), "reject"),
        (timedelta(0), "reject"),
    ],
)
def test_risk_rolling_hour_boundaries(
    age: timedelta,
    status: str,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    for _ in range(5):
        _risk_history(risk_state, amount="1.00", completed_at=risk_now - age)
    assert _risk_evaluate(risk_policy, risk_state, risk_now).status == status


def test_risk_velocity_uses_elapsed_time_across_dst_offsets(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
) -> None:
    from zoneinfo import ZoneInfo

    now = datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1)
    completed = datetime.fromisoformat("2026-11-01T01:15:00-05:00")
    for _ in range(5):
        _risk_history(risk_state, amount="1.00", completed_at=completed)
    assert _risk_evaluate(risk_policy, risk_state, now).reason == "velocity_exceeded"


def test_risk_daily_reset_uses_midnight_singapore_even_with_utc_input(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
) -> None:
    midnight = datetime.fromisoformat("2026-10-14T16:00:00+00:00")
    _risk_history(risk_state, amount="20000.00", completed_at=midnight - timedelta(microseconds=1))
    before = _risk_evaluate(risk_policy, risk_state, midnight - timedelta(microseconds=1))
    assert before.reason == "daily_limit_exceeded"
    assert _risk_evaluate(risk_policy, risk_state, midnight).status == "allow"
    _risk_history(risk_state, amount="20000.00", completed_at=midnight)
    assert _risk_evaluate(risk_policy, risk_state, midnight).reason == "daily_limit_exceeded"


def test_risk_velocity_does_not_reset_at_midnight(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
) -> None:
    midnight = datetime.fromisoformat("2026-10-15T00:00:00+08:00")
    for index in range(5):
        _risk_history(
            risk_state, amount="1.00", completed_at=midnight - timedelta(minutes=index + 1)
        )
    assert _risk_evaluate(risk_policy, risk_state, midnight).reason == "velocity_exceeded"


def test_risk_ignores_client_draft_time_and_descriptive_seed_history(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    draft = _risk_draft().model_copy(
        update={"created_at": datetime.fromisoformat("2000-01-01T00:00:00+00:00")}
    )
    assert _risk_evaluate(risk_policy, risk_state, risk_now, draft=draft).status == "allow"
    _risk_history(risk_state, amount="20000.00", completed_at=risk_now - timedelta(hours=2))
    assert (
        _risk_evaluate(risk_policy, risk_state, risk_now, draft=draft).reason
        == "daily_limit_exceeded"
    )


def test_risk_does_not_charge_another_customers_history(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    other = copy.deepcopy(risk_state["users"]["u_demo"])
    other["user_id"] = "u_other"
    identifiers = {a["account_id"]: "acc_other_" + a["account_id"] for a in other["accounts"]}
    for account in other["accounts"]:
        account["account_id"] = identifiers[account["account_id"]]
    for holding in other["holdings"]:
        holding["account_id"] = identifiers[holding["account_id"]]
    risk_state["users"]["u_other"] = other
    for _ in range(5):
        _risk_history(
            risk_state,
            amount="20000.00",
            user_id="u_other",
            account_id="acc_other_acc_5678",
            completed_at=risk_now,
        )
    assert _risk_evaluate(risk_policy, risk_state, risk_now).status == "allow"
    foreign = _risk_draft(from_account_id="acc_other_acc_5678")
    assert (
        _risk_evaluate(risk_policy, risk_state, risk_now, draft=foreign).reason
        == "account_not_owned"
    )


def test_risk_keeps_currency_totals_separate_but_counts_customer_transfer_frequency(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    # A fictional historical USD current account; no live USD policy is enabled.
    risk_state["users"]["u_demo"]["accounts"].append(
        {
            "account_id": "acc_usd_history",
            "label": "Fictional USD current",
            "type": "current",
            "currency": "USD",
            "balance": "30000.00",
            "status": "active",
        }
    )
    for _ in range(5):
        _risk_history(
            risk_state,
            amount="20000.00",
            account_id="acc_usd_history",
            completed_at=risk_now - timedelta(hours=2),
        )
    assert _risk_evaluate(risk_policy, risk_state, risk_now).status == "allow"
    for _ in range(5):
        _risk_history(
            risk_state, amount="1.00", account_id="acc_usd_history", completed_at=risk_now
        )
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "velocity_exceeded"


@pytest.mark.parametrize(
    "kyc", [None, {}, {"status": "incomplete"}, {"status": "pending"}, {"status": True}, "complete"]
)
def test_risk_invalid_or_missing_kyc_rejects(
    kyc: Any, risk_policy: rules.TransferPolicy, risk_state: dict[str, Any], risk_now: datetime
) -> None:
    risk_state["users"]["u_demo"]["kyc"] = kyc
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "kyc_incomplete"


@pytest.mark.parametrize("status", [None, "inactive", "frozen", "closed", True])
def test_risk_inactive_or_missing_account_status_rejects(
    status: Any, risk_policy: rules.TransferPolicy, risk_state: dict[str, Any], risk_now: datetime
) -> None:
    risk_state["users"]["u_demo"]["accounts"][1]["status"] = status
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "account_inactive"


def test_risk_account_ownership_type_currency_and_label_checks(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    assert (
        _risk_evaluate(
            risk_policy, risk_state, risk_now, draft=_risk_draft(from_account_id="acc_foreign")
        ).reason
        == "account_not_owned"
    )
    for field, value, reason in (
        ("type", "investment", "account_type_invalid"),
        ("currency", "USD", "currency_mismatch"),
        ("label", "Wrong label", "account_label_mismatch"),
    ):
        changed = copy.deepcopy(risk_state)
        changed["users"]["u_demo"]["accounts"][1][field] = value
        assert _risk_evaluate(risk_policy, changed, risk_now).reason == reason
    assert (
        _risk_evaluate(
            risk_policy,
            risk_state,
            risk_now,
            draft=_risk_draft().model_copy(update={"user_id": "u_unknown"}),
        ).reason
        == "unknown_user"
    )


@pytest.mark.parametrize(
    "action",
    [
        _ledger_action("bill_payment"),
        _ledger_action("stock_order"),
        _ledger_action("fx_conversion"),
        _ledger_action(currency="USD"),
        _ledger_action(currency="EUR"),
    ],
)
def test_risk_unsupported_policies_fail_closed(
    action: dict[str, Any],
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    assert _risk_evaluate(
        risk_policy, risk_state, risk_now, draft=_ledger_draft(action)
    ).model_dump() == {"status": "reject", "reason": "policy_unsupported", "method": "otp"}


def test_risk_mixed_batch_cannot_silently_exempt_unsupported_action(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    draft = _ledger_draft(_ledger_action(), _ledger_action("bill_payment"))
    assert (
        _risk_evaluate(risk_policy, risk_state, risk_now, draft=draft).reason
        == "policy_unsupported"
    )


@pytest.mark.parametrize("collection", ["users", "transactions", "executions", "outbox"])
def test_risk_missing_required_state_fails_closed(
    collection: str,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    risk_state.pop(collection)
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "state_invalid"


@pytest.mark.parametrize(
    "collection,value",
    [
        ("users", []),
        ("transactions", None),
        ("transactions", {}),
        ("executions", []),
        ("outbox", None),
    ],
)
def test_risk_invalid_required_state_fails_closed(
    collection: str,
    value: Any,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    risk_state[collection] = value
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "state_invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("completed_at", "not a time"),
        ("completed_at", "2026-10-14T11:00:00"),
        ("execution_id", "ex_uncommitted"),
        ("reference", "tx_forged"),
        ("status", "pending"),
    ],
)
def test_risk_invalid_or_uncommitted_history_is_not_treated_as_zero_usage(
    field: str,
    value: Any,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    _risk_history(risk_state, amount="100.00", completed_at=risk_now)
    risk_state["transactions"][0][field] = value
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "state_invalid"


@pytest.mark.parametrize("amount", ["-1.00", "NaN", 1.00, "1.001", "0.00"])
def test_risk_invalid_committed_money_fails_closed(
    amount: Any, risk_policy: rules.TransferPolicy, risk_state: dict[str, Any], risk_now: datetime
) -> None:
    _risk_history(risk_state, amount="100.00", completed_at=risk_now)
    risk_state["transactions"][0]["action"]["amount"] = amount
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "state_invalid"


@pytest.mark.parametrize("now", [None, "2026-10-14T12:00:00+08:00", datetime(2026, 10, 14), True])
def test_risk_requires_aware_backend_datetime(
    now: Any, risk_policy: rules.TransferPolicy, risk_state: dict[str, Any]
) -> None:
    assert _risk_evaluate(risk_policy, risk_state, now).reason == "clock_invalid"


def test_risk_future_commits_or_clock_regression_fail_closed(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    _risk_history(risk_state, amount="100.00", completed_at=risk_now + timedelta(microseconds=1))
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "clock_invalid"


def test_risk_identical_retry_returns_already_committed_without_new_usage(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    for _ in range(4):
        _risk_history(risk_state, amount="4000.00", completed_at=risk_now)
    draft = _risk_history(
        risk_state, amount="4000.00", completed_at=risk_now, execution_id="ex_repeat"
    )
    before = copy.deepcopy(risk_state)
    for _ in range(3):
        assert _risk_evaluate(
            risk_policy, risk_state, risk_now, draft=draft, execution_id="ex_repeat"
        ).model_dump() == {"status": "allow", "reason": "already_committed", "method": "otp"}
    assert risk_state == before
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "daily_limit_exceeded"


@pytest.mark.parametrize(
    "changes",
    [{"amount": "101.00"}, {"to_payee_id": "pay_8892", "to_payee_label": "John Smith …8892"}],
)
def test_risk_execution_identity_conflicts_bind_complete_draft(
    changes: dict[str, Any],
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    _risk_history(risk_state, amount="100.00", completed_at=risk_now, execution_id="ex_original")
    assert (
        _risk_evaluate(
            risk_policy,
            risk_state,
            risk_now,
            draft=_risk_draft(**changes),
            execution_id="ex_original",
        ).reason
        == "execution_conflict"
    )


def test_risk_precision_remains_exact_with_low_decimal_context(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    _risk_history(risk_state, amount="19999.99", completed_at=risk_now - timedelta(hours=2))
    with localcontext() as context:
        context.prec = 3
        assert _risk_evaluate(risk_policy, risk_state, risk_now, "0.01").status == "allow"
        assert (
            _risk_evaluate(risk_policy, risk_state, risk_now, "0.02").reason
            == "daily_limit_exceeded"
        )


def test_risk_inputs_cannot_supply_otp_verification_or_bypass_flags(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    for field, value in (("otp", "fictional-code"), ("verified", True), ("skip_risk", True)):
        with pytest.raises(TypeError):
            rules.evaluate_risk(
                _risk_draft("50000.00"),
                execution_id="ex_no_bypass",
                policy=risk_policy,
                state=risk_state,
                now=risk_now,
                **{field: value},
            )
    assert (
        _risk_evaluate(risk_policy, risk_state, risk_now, "50000.00").reason
        == "per_transfer_limit_exceeded"
    )
    for _ in range(3):
        assert (
            _risk_evaluate(risk_policy, risk_state, risk_now, "8000.00").status
            == "needs_extra_check"
        )


def test_risk_revalidates_mutated_drafts_and_execution_ids(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    draft = _risk_draft()
    draft.actions[0].amount = 1.5
    assert _risk_evaluate(risk_policy, risk_state, risk_now, draft=draft).reason == "draft_invalid"
    for identifier in ("", "ex_", "unsafe", None):
        assert (
            _risk_evaluate(risk_policy, risk_state, risk_now, execution_id=identifier).reason
            == "draft_invalid"
        )


def test_risk_evaluation_never_mutates_snapshots_or_persistent_data(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
    ledger_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = copy.deepcopy(risk_state)
    storage = {
        p.name: p.read_bytes() for p in ledger_store["storage_path"].parent.iterdir() if p.is_file()
    }

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Risk evaluation cannot read files, write, execute or contact models")

    monkeypatch.setattr(ledger, "execute_batch", forbidden)
    monkeypatch.setattr(ledger, "reconcile_audit", forbidden)
    monkeypatch.setattr(audit, "append_event", forbidden)
    monkeypatch.setattr(readonly, "_load_database", forbidden)
    monkeypatch.setattr(readonly, "get_ledger_snapshot", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(ledger.os, "replace", forbidden)
    monkeypatch.setattr(ledger.os, "fsync", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    for amount in ("100.00", "8000.00", "50000.00"):
        assert set(_risk_evaluate(risk_policy, risk_state, risk_now, amount).model_dump()) == {
            "status",
            "reason",
            "method",
        }
    assert risk_state == before
    assert {
        p.name: p.read_bytes() for p in ledger_store["storage_path"].parent.iterdir() if p.is_file()
    } == storage


def test_risk_decisions_expose_only_controlled_internal_codes(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    draft = _risk_draft().model_copy(
        update={"transcript": "Fictional private instruction OTP-secret"}
    )
    result = _risk_evaluate(risk_policy, risk_state, risk_now, draft=draft)
    assert result.model_dump() == {"status": "allow", "reason": "allowed", "method": "otp"}
    assert "OTP-secret" not in result.model_dump_json()
    with pytest.raises(ValidationError):
        result.status = "reject"


def test_risk_module_has_no_execution_or_ai_imports() -> None:
    source = Path(rules.__file__).read_text()
    imports = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.extend((node.module or "") + "." + alias.name for alias in node.names)
    assert not any(
        name.startswith(
            (
                "app.bank.ledger",
                "app.bank.gateway",
                "app.intent",
                "app.validation",
                "app.llm",
                "httpx",
            )
        )
        for name in imports
    )


def test_risk_uses_explicit_configuration_instead_of_hardcoded_limits(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    policy = rules.TransferPolicy.model_validate(
        {
            **risk_policy.model_dump(mode="python"),
            "per_transfer_max": "10000.00",
            "daily_transfer_max": "30000.00",
            "step_up_above": "1000.00",
            "velocity_max_transfers": 2,
            "velocity_window_minutes": 15,
        }
    )
    _risk_history(risk_state, amount="1.00", completed_at=risk_now - timedelta(minutes=20))
    _risk_history(risk_state, amount="1.00", completed_at=risk_now - timedelta(minutes=1))
    assert _risk_evaluate(policy, risk_state, risk_now, "1000.00").status == "allow"
    assert _risk_evaluate(policy, risk_state, risk_now, "1000.01").reason == "step_up_required"
    assert (
        _risk_evaluate(policy, risk_state, risk_now, "10000.01").reason
        == "per_transfer_limit_exceeded"
    )
    _risk_history(risk_state, amount="1.00", completed_at=risk_now)
    assert _risk_evaluate(policy, risk_state, risk_now).reason == "velocity_exceeded"


@pytest.mark.usefixtures("financial_kernel")
def test_risk_evaluator_accepts_actual_committed_j08_records_and_retry_fingerprint(
    risk_policy: rules.TransferPolicy,
    ledger_store: dict[str, Path],
) -> None:
    draft = _risk_draft()
    ledger.execute_batch(draft, "ex_real_risk_record", **ledger_store)
    state = _ledger_state(ledger_store)
    now = datetime.fromisoformat(state["transactions"][0]["completed_at"]) + timedelta(seconds=1)
    assert (
        _risk_evaluate(
            risk_policy, state, now, draft=draft, execution_id="ex_real_risk_record"
        ).reason
        == "already_committed"
    )
    assert _risk_evaluate(risk_policy, state, now).status == "allow"
    assert _ledger_balance(ledger_store, "acc_5678") == "64900.00"


@pytest.mark.parametrize(
    "changes",
    [
        {"transcript": "Changed instruction"},
        {"session_id": "s_other_session"},
        {"created_at": datetime.fromisoformat("2000-01-01T00:00:00+00:00")},
    ],
)
def test_risk_retry_binds_transcript_session_and_created_at(
    changes: dict[str, Any],
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    original = _risk_history(
        risk_state, amount="100.00", completed_at=risk_now, execution_id="ex_bound"
    )
    assert (
        _risk_evaluate(
            risk_policy,
            risk_state,
            risk_now,
            draft=original.model_copy(update=changes),
            execution_id="ex_bound",
        ).reason
        == "execution_conflict"
    )


def test_risk_inconsistent_retry_cache_cannot_claim_another_financial_payload(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    original = _risk_history(
        risk_state, amount="100.00", completed_at=risk_now, execution_id="ex_cache"
    )
    risk_state["transactions"][0]["action"]["amount"] = "101.00"
    assert (
        _risk_evaluate(
            risk_policy, risk_state, risk_now, draft=original, execution_id="ex_cache"
        ).reason
        == "state_invalid"
    )


def test_risk_duplicate_financial_records_fail_closed_instead_of_distorting_counters(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    _risk_history(risk_state, amount="100.00", completed_at=risk_now)
    risk_state["transactions"].append(copy.deepcopy(risk_state["transactions"][0]))
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "state_invalid"


def test_risk_missing_account_collections_and_kyc_fail_closed(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    risk_state["users"]["u_demo"].pop("kyc")
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "kyc_incomplete"
    risk_state["users"]["u_demo"].pop("accounts")
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "state_invalid"


def test_risk_changed_eligibility_does_not_turn_committed_retry_into_new_usage(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    draft = _risk_history(
        risk_state, amount="100.00", completed_at=risk_now, execution_id="ex_done"
    )
    risk_state["users"]["u_demo"]["kyc"]["status"] = "incomplete"
    before = copy.deepcopy(risk_state)
    assert (
        _risk_evaluate(
            risk_policy, risk_state, risk_now, draft=draft, execution_id="ex_done"
        ).reason
        == "already_committed"
    )
    assert _risk_evaluate(risk_policy, risk_state, risk_now).reason == "kyc_incomplete"
    assert risk_state == before


def test_risk_needs_a_new_snapshot_to_observe_a_later_commit(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    # Phase 1 cannot make snapshot-based decisions atomic. Phase 2 owns the lock.
    _risk_history(risk_state, amount="15000.00", completed_at=risk_now - timedelta(hours=2))
    assert _risk_evaluate(risk_policy, risk_state, risk_now, "5000.00").status == "allow"
    updated = copy.deepcopy(risk_state)
    _risk_history(updated, amount="5000.00", completed_at=risk_now)
    assert _risk_evaluate(risk_policy, updated, risk_now).reason == "daily_limit_exceeded"


def test_risk_datetime_underflow_is_rejected_without_an_exception(
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
) -> None:
    now = datetime.fromisoformat("0001-01-01T00:00:00+00:00")
    assert _risk_evaluate(risk_policy, risk_state, now).reason == "clock_invalid"


@pytest.fixture
def risk_execution_store(
    ledger_paths: dict[str, Path],
    risk_policy: rules.TransferPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    """Use the real evaluator, with proposed policy installed only in the test backend."""
    monkeypatch.setattr(rules, "execution_policy", lambda: risk_policy)
    ledger.reconcile_audit(**ledger_paths)
    ledger.set_payment_pause(False, operation_id="op_test_enable", **ledger_paths)
    return ledger_paths


def _financial_state(paths: dict[str, Path]) -> dict[str, Any]:
    state = _ledger_state(paths)
    return {key: state[key] for key in ("users", "transactions", "executions", "next_reference")}


def _risk_execution_result(paths: dict[str, Path], identifier: str, draft: Draft) -> str:
    try:
        ledger.execute_batch(draft, identifier, **paths)
        return "executed"
    except ledger.LedgerRejected as error:
        return error.reason


def _bank_clock(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    class BackendClock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(ledger, "datetime", BackendClock)


def test_enforcement_fresh_state_is_paused_and_unconfigured(
    ledger_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert rules.execution_policy() is None
    calls = []
    evaluate = rules.evaluate_risk

    def observed(*args: Any, **kwargs: Any) -> rules.RiskDecision:
        calls.append(kwargs)
        return evaluate(*args, **kwargs)

    monkeypatch.setattr(rules, "evaluate_risk", observed)
    with pytest.raises(ledger.LedgerRiskRejected, match="payments_paused"):
        ledger.execute_batch(_risk_draft(), "ex_default_paused", **ledger_paths)
    assert len(calls) == 1 and calls[0]["policy"] is None
    assert calls[0]["now"].utcoffset() is not None
    assert _ledger_state(ledger_paths)["payment_control"] == {"paused": True}
    assert _ledger_state(ledger_paths)["transactions"] == []
    with pytest.raises(ledger.LedgerRejected, match="policy_missing"):
        ledger.set_payment_pause(False, operation_id="op_unapproved", **ledger_paths)
    assert _ledger_state(ledger_paths)["payment_control"]["paused"] is True


@pytest.mark.parametrize(
    "amount,status,reason",
    [
        ("50000.00", "reject", "per_transfer_limit_exceeded"),
        ("20000.01", "reject", "per_transfer_limit_exceeded"),
        ("8000.00", "needs_extra_check", "step_up_required"),
        ("5000.01", "needs_extra_check", "step_up_required"),
    ],
)
def test_enforcement_hard_limits_and_step_up_never_commit(
    risk_execution_store: dict[str, Path],
    amount: str,
    status: str,
    reason: str,
) -> None:
    before = _financial_state(risk_execution_store)
    with pytest.raises(ledger.LedgerRiskRejected) as error:
        ledger.execute_batch(_risk_draft(amount), "ex_blocked_limit", **risk_execution_store)
    assert (error.value.status, error.value.reason) == (status, reason)
    assert _financial_state(risk_execution_store) == before
    state = _ledger_state(risk_execution_store)
    assert state["outbox"] == []
    assert state["security_events"][-1]["data"]["reason"] == reason


@pytest.mark.parametrize("kind", ["bill_payment", "stock_order", "fx_conversion", "USD", "mixed"])
def test_enforcement_unsupported_policies_fail_closed(
    risk_execution_store: dict[str, Path],
    kind: str,
) -> None:
    before = _financial_state(risk_execution_store)
    if kind == "USD":
        draft = _risk_draft(currency="USD")
    elif kind == "mixed":
        draft = _ledger_draft(_ledger_action(), _ledger_action("bill_payment"))
    else:
        draft = _ledger_draft(_ledger_action(kind))
    assert (
        _risk_execution_result(risk_execution_store, "ex_unsupported", draft)
        == "policy_unsupported"
    )
    assert _financial_state(risk_execution_store) == before


@pytest.mark.parametrize("policy", [None, {}, False])
def test_enforcement_unconfigured_or_invalid_policy_after_enabling_fails_closed(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    policy: Any,
) -> None:
    before = _financial_state(risk_execution_store)
    monkeypatch.setattr(rules, "execution_policy", lambda: policy)
    assert _risk_execution_result(risk_execution_store, "ex_policy_missing", _risk_draft()) == (
        "policy_missing" if policy is None else "policy_invalid"
    )
    assert _financial_state(risk_execution_store) == before
    with pytest.raises(ledger.LedgerRejected, match="policy_missing|policy_invalid"):
        ledger.set_payment_pause(False, operation_id="op_new_resume", **risk_execution_store)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("kyc", "kyc_incomplete"),
        ("status", "account_inactive"),
        ("ownership", "account_not_owned"),
        ("currency", "currency_mismatch"),
    ],
)
def test_enforcement_latest_committed_eligibility_is_required(
    risk_execution_store: dict[str, Path],
    change: str,
    reason: str,
) -> None:
    state = _ledger_state(risk_execution_store)
    draft = _risk_draft()
    if change == "kyc":
        state["users"]["u_demo"]["kyc"]["status"] = "pending"
    elif change == "status":
        state["users"]["u_demo"]["accounts"][1]["status"] = "inactive"
    elif change == "currency":
        state["users"]["u_demo"]["accounts"][1]["currency"] = "USD"
    else:
        draft = _risk_draft(from_account_id="acc_other")
    ledger._save_state(risk_execution_store["storage_path"], state)
    before = _financial_state(risk_execution_store)
    assert _risk_execution_result(risk_execution_store, "ex_eligibility", draft) == reason
    assert _financial_state(risk_execution_store) == before


@pytest.mark.parametrize(
    "amount,workers,allowed,reason",
    [
        ("5000.00", 6, 4, "daily_limit_exceeded"),
        ("100.00", 8, 5, "velocity_exceeded"),
    ],
)
def test_enforcement_concurrent_daily_and_velocity_limits_are_atomic(
    risk_execution_store: dict[str, Path],
    amount: str,
    workers: int,
    allowed: int,
    reason: str,
) -> None:
    barrier = Barrier(workers)

    def transfer(index: int) -> str:
        barrier.wait(timeout=10)
        return _risk_execution_result(
            risk_execution_store, f"ex_racing_{index}", _risk_draft(amount)
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(transfer, range(workers)))
    assert outcomes.count("executed") == allowed
    assert outcomes.count(reason) == workers - allowed
    state = _ledger_state(risk_execution_store)
    assert len(state["transactions"]) == len(state["executions"]) == allowed
    assert Decimal(_ledger_balance(risk_execution_store, "acc_5678")) == (
        Decimal("65000.00") - Decimal(amount) * allowed
    )
    assert audit.read_events(100, storage_path=risk_execution_store["audit_path"]).chain_valid


def test_enforcement_daily_limit_is_shared_across_customer_accounts(
    risk_execution_store: dict[str, Path],
) -> None:
    for index in range(4):
        ledger.execute_batch(_risk_draft("4000.00"), f"ex_current_{index}", **risk_execution_store)
    ledger.execute_batch(
        _ledger_draft(_ledger_action(amount="3000.00")), "ex_savings", **risk_execution_store
    )
    before = _financial_state(risk_execution_store)
    assert (
        _risk_execution_result(risk_execution_store, "ex_over_daily", _risk_draft("1000.01"))
        == "daily_limit_exceeded"
    )
    assert _financial_state(risk_execution_store) == before
    assert _ledger_balance(risk_execution_store) == "200.00"


def test_enforcement_midnight_reset_uses_backend_singapore_time(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_midnight = datetime.fromisoformat("2026-10-14T23:30:00+08:00")
    _bank_clock(monkeypatch, before_midnight)
    for index in range(4):
        ledger.execute_batch(
            _risk_draft("5000.00"), f"ex_yesterday_{index}", **risk_execution_store
        )
    _bank_clock(monkeypatch, datetime.fromisoformat("2026-10-14T16:00:00+00:00"))
    ledger.execute_batch(_risk_draft("5000.00"), "ex_today", **risk_execution_store)
    assert (
        _risk_execution_result(risk_execution_store, "ex_hour_full", _risk_draft("1.00"))
        == "velocity_exceeded"
    )
    _bank_clock(monkeypatch, datetime.fromisoformat("2026-10-15T01:00:00+08:00"))
    ledger.execute_batch(_risk_draft("5000.00"), "ex_next_hour", **risk_execution_store)
    assert len(_ledger_state(risk_execution_store)["transactions"]) == 6


def test_enforcement_exact_retries_survive_pause_and_missing_policy_without_new_usage(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = _risk_draft()
    first = ledger.execute_batch(draft, "ex_once_with_risk", **risk_execution_store)
    ledger.set_payment_pause(True, operation_id="op_stop", **risk_execution_store)
    before = _financial_state(risk_execution_store)
    monkeypatch.setattr(rules, "execution_policy", lambda: None)
    assert ledger.execute_batch(draft, "ex_once_with_risk", **risk_execution_store) == first
    assert _financial_state(risk_execution_store) == before
    assert _risk_execution_result(risk_execution_store, "ex_new_paused", draft) == "payments_paused"
    assert (
        _risk_execution_result(risk_execution_store, "ex_once_with_risk", _risk_draft("101.00"))
        == "execution_conflict"
    )
    assert len(_ledger_state(risk_execution_store)["transactions"]) == 1


def test_enforcement_committed_retries_do_not_exhaust_velocity(
    risk_execution_store: dict[str, Path],
) -> None:
    for index in range(5):
        response = ledger.execute_batch(_risk_draft(), f"ex_slot_{index}", **risk_execution_store)
        for _ in range(3):
            assert (
                ledger.execute_batch(_risk_draft(), f"ex_slot_{index}", **risk_execution_store)
                == response
            )
    assert (
        _risk_execution_result(risk_execution_store, "ex_sixth", _risk_draft())
        == "velocity_exceeded"
    )
    assert len(_ledger_state(risk_execution_store)["transactions"]) == 5


def test_enforcement_repeat_blocked_decision_reuses_audit_evidence(
    risk_execution_store: dict[str, Path],
) -> None:
    for _ in range(3):
        assert (
            _risk_execution_result(risk_execution_store, "ex_step_up", _risk_draft("8000.00"))
            == "step_up_required"
        )
    events = audit.read_events(100, storage_path=risk_execution_store["audit_path"]).entries
    assert sum(e.event == "step_up_required" for e in events) == 1
    assert _ledger_state(risk_execution_store)["transactions"] == []


def test_enforcement_real_transfer_updates_account_api_and_forecast(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        readonly, "_ledger_path", lambda path: path or risk_execution_store["storage_path"]
    )
    before = _runtime_forecast()
    ledger.execute_batch(_ledger_draft(), "ex_risk_api", **risk_execution_store)
    response = TestClient(app).get("/api/accounts?user_id=u_demo")
    assert response.status_code == 200
    assert response.json()["accounts"][0]["balance"] == "3100.00"
    assert (
        _runtime_forecast()["projected_balance_after"]
        == f"{Decimal(before['projected_balance_after']) - Decimal('100.00'):.2f}"
    )
    unchanged = response.json(), _runtime_forecast()
    assert (
        _risk_execution_result(
            risk_execution_store, "ex_risk_bill", _ledger_draft(_ledger_action("bill_payment"))
        )
        == "policy_unsupported"
    )
    assert (
        TestClient(app).get("/api/accounts?user_id=u_demo").json(),
        _runtime_forecast(),
    ) == unchanged
    assert "payment_control" not in response.text and "security_events" not in response.text


def test_payment_pause_operator_retry_does_not_reverse_later_control(
    risk_execution_store: dict[str, Path],
) -> None:
    ledger.set_payment_pause(True, operation_id="op_stop", **risk_execution_store)
    ledger.set_payment_pause(False, operation_id="op_restart", **risk_execution_store)
    ledger.set_payment_pause(True, operation_id="op_stop", **risk_execution_store)
    assert _ledger_state(risk_execution_store)["payment_control"]["paused"] is False
    with pytest.raises(ledger.LedgerRejected, match="control_operation_conflict"):
        ledger.set_payment_pause(False, operation_id="op_stop", **risk_execution_store)
    assert _ledger_state(risk_execution_store)["payment_control"]["paused"] is False
    events = audit.read_events(100, storage_path=risk_execution_store["audit_path"]).entries
    assert sum(e.event == "kill_switch_activated" for e in events) == 1
    assert sum(e.event == "kill_switch_deactivated" for e in events) == 2


@pytest.mark.parametrize(
    "paused,operation", [("false", "op_x"), (0, "op_x"), (False, "bad"), (True, None)]
)
def test_payment_pause_invalid_operator_inputs_cannot_write(
    ledger_paths: dict[str, Path],
    paused: Any,
    operation: Any,
) -> None:
    with pytest.raises(ledger.LedgerRejected, match="invalid_operator_control"):
        ledger.set_payment_pause(paused, operation_id=operation, **ledger_paths)
    assert not ledger_paths["storage_path"].exists()


@pytest.mark.parametrize("first", ["execution", "pause"])
def test_payment_pause_racing_with_execution_uses_same_lock(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    entered, release, competing = Event(), Event(), Event()
    if first == "execution":
        stage = ledger._stage

        def held_stage(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            assert release.wait(timeout=10)
            return stage(*args, **kwargs)

        monkeypatch.setattr(ledger, "_stage", held_stage)
    else:
        save = ledger._save_state

        def held_save(path: Path, state: dict[str, Any]) -> None:
            if state["payment_control"]["paused"]:
                entered.set()
                assert release.wait(timeout=10)
            save(path, state)

        monkeypatch.setattr(ledger, "_save_state", held_save)

    def execute() -> str:
        if first == "pause":
            competing.set()
        return _risk_execution_result(risk_execution_store, "ex_pause_race", _risk_draft())

    def pause() -> None:
        if first == "execution":
            competing.set()
        ledger.set_payment_pause(True, operation_id="op_racing_stop", **risk_execution_store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        initial = pool.submit(execute if first == "execution" else pause)
        assert entered.wait(timeout=10)
        following = pool.submit(pause if first == "execution" else execute)
        assert competing.wait(timeout=10)
        release.set()
        outcome = (
            initial.result(timeout=10) if first == "execution" else following.result(timeout=10)
        )
        (following if first == "execution" else initial).result(timeout=10)
    assert outcome == ("executed" if first == "execution" else "payments_paused")
    state = _ledger_state(risk_execution_store)
    assert state["payment_control"]["paused"] is True
    assert len(state["transactions"]) == (1 if first == "execution" else 0)
    assert (
        _risk_execution_result(risk_execution_store, "ex_after_pause", _risk_draft())
        == "payments_paused"
    )


def test_payment_pause_existing_state_migration_preserves_committed_money(
    risk_execution_store: dict[str, Path],
) -> None:
    ledger.execute_batch(_risk_draft(), "ex_before_migration", **risk_execution_store)
    financial = _financial_state(risk_execution_store)
    state = _ledger_state(risk_execution_store)
    # Model an older J-08 snapshot: keep only its transaction audit evidence.
    state.pop("payment_control")
    state.pop("security_events")
    ledger._save_state(risk_execution_store["storage_path"], state)
    before_read = risk_execution_store["storage_path"].read_bytes()
    assert readonly.get_ledger_snapshot("u_demo", storage_path=risk_execution_store["storage_path"])
    assert risk_execution_store["storage_path"].read_bytes() == before_read
    assert (
        _risk_execution_result(risk_execution_store, "ex_legacy_denied", _risk_draft())
        == "payments_paused"
    )
    assert _financial_state(risk_execution_store) == financial
    ledger.set_payment_pause(True, operation_id="op_migrate_paused", **risk_execution_store)
    assert _ledger_state(risk_execution_store)["payment_control"] == {"paused": True}
    ledger.set_payment_pause(False, operation_id="op_migrate_enable", **risk_execution_store)
    assert _financial_state(risk_execution_store) == financial
    ledger.execute_batch(_risk_draft(), "ex_after_migration", **risk_execution_store)
    assert len(_ledger_state(risk_execution_store)["transactions"]) == 2


@pytest.mark.parametrize(
    "control",
    [
        None,
        {},
        {"paused": None},
        {"paused": "false"},
        {"paused": 0},
        {"paused": False, "skip": True},
    ],
)
def test_payment_pause_corrupt_controls_never_reset_or_allow_execution(
    risk_execution_store: dict[str, Path],
    control: Any,
) -> None:
    state = _ledger_state(risk_execution_store)
    state["payment_control"] = control
    ledger._save_state(risk_execution_store["storage_path"], state)
    before = risk_execution_store["storage_path"].read_bytes()
    with pytest.raises(readonly.LedgerStorageError):
        ledger.execute_batch(_risk_draft(), "ex_corrupt_control", **risk_execution_store)
    with pytest.raises(readonly.LedgerStorageError):
        ledger.set_payment_pause(False, operation_id="op_corrupt_resume", **risk_execution_store)
    assert risk_execution_store["storage_path"].read_bytes() == before


def test_payment_pause_missing_control_with_previous_resume_still_defaults_paused(
    risk_execution_store: dict[str, Path],
) -> None:
    state = _ledger_state(risk_execution_store)
    state.pop("payment_control")
    ledger._save_state(risk_execution_store["storage_path"], state)
    assert (
        _risk_execution_result(risk_execution_store, "ex_missing_control", _risk_draft())
        == "payments_paused"
    )
    assert _ledger_state(risk_execution_store)["transactions"] == []


@pytest.mark.parametrize("action", ["pause", "risk", "execution"])
def test_enforcement_audit_delivery_failure_is_durable_and_recoverable(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    financial = _financial_state(risk_execution_store)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise audit.AuditError("Unavailable")

    def perform() -> Any:
        if action == "pause":
            return ledger.set_payment_pause(
                True, operation_id="op_audit_stop", **risk_execution_store
            )
        return ledger.execute_batch(
            _risk_draft("8000.00" if action == "risk" else "100.00"),
            "ex_audit_recover",
            **risk_execution_store,
        )

    with monkeypatch.context() as patch:
        patch.setattr(audit, "append_event", unavailable)
        with pytest.raises(ledger.LedgerAuditPending):
            perform()
        state = _ledger_state(risk_execution_store)
        assert len(state["outbox"]) == 1
        before = _financial_state(risk_execution_store)
        with pytest.raises(ledger.LedgerAuditPending):
            ledger.execute_batch(_risk_draft(), "ex_no_audit_bypass", **risk_execution_store)
        assert _financial_state(risk_execution_store) == before
    if action == "risk":
        with pytest.raises(ledger.LedgerRiskRejected, match="step_up_required"):
            perform()
    else:
        perform()
    state = _ledger_state(risk_execution_store)
    assert state["outbox"] == []
    if action == "execution":
        assert len(state["transactions"]) == 1
        assert _ledger_balance(risk_execution_store, "acc_5678") == "64900.00"
    else:
        assert _financial_state(risk_execution_store) == financial
    log = audit.read_events(100, storage_path=risk_execution_store["audit_path"])
    assert log.chain_valid
    assert len(log.entries) == 2


def test_payment_pause_can_activate_during_audit_corruption_but_resume_cannot(
    risk_execution_store: dict[str, Path],
) -> None:
    risk_execution_store["audit_path"].write_text("corrupted\n")
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.set_payment_pause(True, operation_id="op_emergency", **risk_execution_store)
    assert _ledger_state(risk_execution_store)["payment_control"]["paused"] is True
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.set_payment_pause(False, operation_id="op_unsafe_resume", **risk_execution_store)
    assert _ledger_state(risk_execution_store)["transactions"] == []


@pytest.mark.parametrize("action", ["pause", "risk", "execution"])
def test_enforcement_crash_after_audit_before_ack_reuses_logical_event(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    save = ledger._save_state

    def fail_ack(path: Path, state: dict[str, Any]) -> None:
        if not state["outbox"]:
            raise readonly.LedgerStorageError("Simulated crash before acknowledgement")
        save(path, state)

    def perform() -> Any:
        if action == "pause":
            return ledger.set_payment_pause(
                True, operation_id="op_ack_stop", **risk_execution_store
            )
        return ledger.execute_batch(
            _risk_draft("8000.00" if action == "risk" else "100.00"),
            "ex_ack_recover",
            **risk_execution_store,
        )

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_save_state", fail_ack)
        with pytest.raises(readonly.LedgerStorageError):
            perform()
    evidence = risk_execution_store["audit_path"].read_bytes()
    assert len(_ledger_state(risk_execution_store)["outbox"]) == 1
    if action == "risk":
        with pytest.raises(ledger.LedgerRiskRejected):
            perform()
    else:
        perform()
    assert risk_execution_store["audit_path"].read_bytes() == evidence
    assert _ledger_state(risk_execution_store)["outbox"] == []
    assert len(_ledger_state(risk_execution_store)["transactions"]) == (
        1 if action == "execution" else 0
    )


@pytest.mark.parametrize("action", ["pause", "risk", "execution"])
def test_enforcement_write_failure_before_replace_preserves_financial_state(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    before = risk_execution_store["storage_path"].read_bytes()

    def fail_replace(*args: Any, **kwargs: Any) -> None:
        raise OSError("Disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(ledger.os, "replace", fail_replace)
        with pytest.raises(readonly.LedgerStorageError):
            if action == "pause":
                ledger.set_payment_pause(
                    True, operation_id="op_failed_stop", **risk_execution_store
                )
            else:
                ledger.execute_batch(
                    _risk_draft("8000.00" if action == "risk" else "100.00"),
                    "ex_failed_replace",
                    **risk_execution_store,
                )
    assert risk_execution_store["storage_path"].read_bytes() == before
    assert _ledger_state(risk_execution_store)["transactions"] == []


def test_enforcement_controls_have_no_public_or_ai_access_and_no_bypass_arguments() -> None:
    import inspect

    from app.bank import router

    assert "set_payment_pause" not in vars(router)
    assert "ledger" not in vars(router)
    assert not any("pause" in route.path for route in router.router.routes)
    arguments = inspect.signature(ledger.execute_batch).parameters
    assert set(arguments) == {
        "draft",
        "execution_id",
        "storage_path",
        "audit_path",
        "step_up_satisfied",
    }
    assert set(inspect.signature(rules.execution_policy).parameters) == set()


def test_enforcement_audits_exclude_sensitive_inputs(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("No network or AI allowed in risk enforcement")

    monkeypatch.setattr(socket, "socket", no_network)
    draft = _risk_draft("8000.00").model_copy(
        update={"transcript": "OTP secret 999123 sensitive fictional instruction"}
    )
    assert (
        _risk_execution_result(risk_execution_store, "ex_private_risk", draft) == "step_up_required"
    )
    text = risk_execution_store["audit_path"].read_text()
    for forbidden in ("999123", "sensitive fictional", "8000", "u_demo", "acc_5678", "John Doe"):
        assert forbidden not in text
    assert "step_up_required" in text


_RISK_BACKEND_PROCESS = """
import json, sys
from pathlib import Path
from app.bank import ledger, rules
from app.contracts import Draft
# Approved fictional fixtures for this test process only, never client policy inputs.
policy = rules.TransferPolicy.model_validate(json.loads(sys.argv[1]))
rules.execution_policy = lambda: policy
paths = dict(storage_path=Path(sys.argv[4]), audit_path=Path(sys.argv[5]))
draft = Draft.model_validate(json.loads(sys.argv[2]))
identifier = sys.argv[3]
"""


@pytest.mark.parametrize(
    "amount,reason", [("5000.00", "daily_limit_exceeded"), ("100.00", "velocity_exceeded")]
)
def test_enforcement_processes_cannot_bypass_committed_daily_or_velocity_limits(
    risk_execution_store: dict[str, Path],
    risk_policy: rules.TransferPolicy,
    amount: str,
    reason: str,
) -> None:
    for index in range(3 if amount == "5000.00" else 4):
        ledger.execute_batch(_risk_draft(amount), f"ex_prefill_{index}", **risk_execution_store)
    script = (
        _RISK_BACKEND_PROCESS
        + """
sys.stdin.read(1)
try:
    ledger.execute_batch(draft, identifier, **paths)
    sys.stdout.write("executed")
except ledger.LedgerRiskRejected as error:
    sys.stdout.write(error.reason)
"""
    )
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                risk_policy.model_dump_json(),
                _risk_draft(amount).model_dump_json(),
                f"ex_process_risk_{index}",
                str(risk_execution_store["storage_path"]),
                str(risk_execution_store["audit_path"]),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for index in range(2)
    ]
    try:
        for process in processes:
            process.stdin.write("x")
            process.stdin.flush()
        results = [process.communicate(timeout=20) for process in processes]
        assert all(process.returncode == 0 for process in processes), results
        assert sorted(output for output, _ in results) == sorted(["executed", reason])
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert len(_ledger_state(risk_execution_store)["transactions"]) == (
        4 if amount == "5000.00" else 5
    )


@pytest.mark.parametrize(
    "action,crash",
    [
        ("execution", "replace"),
        ("execution", "ack"),
        ("pause", "replace"),
        ("pause", "ack"),
        ("risk", "ack"),
    ],
)
def test_enforcement_real_process_crash_recovers_without_second_debit_or_event(
    risk_execution_store: dict[str, Path],
    risk_policy: rules.TransferPolicy,
    action: str,
    crash: str,
) -> None:
    script = (
        _RISK_BACKEND_PROCESS
        + """
import os
if sys.argv[7] == "replace":
    ledger._sync_directory = lambda path: os._exit(76)
else:
    save = ledger._save_state
    def crash_ack(path, state):
        if not state["outbox"]:
            os._exit(76)
        save(path, state)
    ledger._save_state = crash_ack
if sys.argv[6] == "pause":
    ledger.set_payment_pause(True, operation_id="op_real_crash_pause", **paths)
else:
    ledger.execute_batch(draft, identifier, **paths)
"""
    )
    draft = _risk_draft("8000.00" if action == "risk" else "100.00")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            risk_policy.model_dump_json(),
            draft.model_dump_json(),
            "ex_real_risk_crash",
            str(risk_execution_store["storage_path"]),
            str(risk_execution_store["audit_path"]),
            action,
            crash,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 76, process.stderr
    financial = _financial_state(risk_execution_store)
    evidence = risk_execution_store["audit_path"].read_bytes()
    assert len(_ledger_state(risk_execution_store)["outbox"]) == 1
    if action == "pause":
        assert _ledger_state(risk_execution_store)["payment_control"]["paused"] is True
        ledger.set_payment_pause(True, operation_id="op_real_crash_pause", **risk_execution_store)
    elif action == "risk":
        with pytest.raises(ledger.LedgerRiskRejected, match="step_up_required"):
            ledger.execute_batch(draft, "ex_real_risk_crash", **risk_execution_store)
    else:
        ledger.execute_batch(draft, "ex_real_risk_crash", **risk_execution_store)
    if crash == "ack":
        assert risk_execution_store["audit_path"].read_bytes() == evidence
    assert _financial_state(risk_execution_store) == financial
    state = _ledger_state(risk_execution_store)
    assert state["outbox"] == []
    assert len(state["transactions"]) == (1 if action == "execution" else 0)
    log = audit.read_events(100, storage_path=risk_execution_store["audit_path"])
    assert len(log.entries) == 2 and log.chain_valid


def test_payment_pause_concurrent_operator_retries_and_risk_retries_reuse_evidence(
    risk_execution_store: dict[str, Path],
) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: ledger.set_payment_pause(
                    True, operation_id="op_concurrent_stop", **risk_execution_store
                ),
                range(8),
            )
        )
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(
            pool.map(
                lambda _: _risk_execution_result(
                    risk_execution_store, "ex_concurrent_denied", _risk_draft()
                ),
                range(8),
            )
        )
    assert outcomes == ["payments_paused"] * 8
    events = audit.read_events(100, storage_path=risk_execution_store["audit_path"]).entries
    assert [e.event for e in events] == [
        "kill_switch_deactivated",
        "kill_switch_activated",
        "risk_rejected",
    ]
    assert _ledger_state(risk_execution_store)["transactions"] == []


@pytest.mark.parametrize(
    "corruption", ["reason", "sensitive", "duplicate", "unknown_event", "missing_ack"]
)
def test_enforcement_security_storage_and_acknowledgement_corruption_fails_closed(
    risk_execution_store: dict[str, Path],
    corruption: str,
) -> None:
    state = _ledger_state(risk_execution_store)
    if corruption == "missing_ack":
        risk_execution_store["audit_path"].write_text("")
    else:
        event = state["security_events"][0]
        if corruption == "reason":
            event["data"]["reason"] = "unsafe customer transcript"
        elif corruption == "sensitive":
            event["data"]["otp"] = "secret"
        elif corruption == "duplicate":
            state["security_events"].append(copy.deepcopy(event))
        else:
            event["event"] = "unknown_control"
        ledger._save_state(risk_execution_store["storage_path"], state)
    original = risk_execution_store["storage_path"].read_bytes()
    with pytest.raises(
        ledger.LedgerAuditPending if corruption == "missing_ack" else readonly.LedgerStorageError
    ):
        ledger.execute_batch(_risk_draft(), "ex_corrupt_security", **risk_execution_store)
    assert risk_execution_store["storage_path"].read_bytes() == original


def test_payment_pause_failed_resume_delivery_blocks_new_commits_until_recovered(
    risk_execution_store: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger.set_payment_pause(True, operation_id="op_before_resume", **risk_execution_store)

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise audit.AuditError("Unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(audit, "append_event", unavailable)
        with pytest.raises(ledger.LedgerAuditPending):
            ledger.set_payment_pause(
                False, operation_id="op_pending_resume", **risk_execution_store
            )
        assert _ledger_state(risk_execution_store)["payment_control"]["paused"] is False
        with pytest.raises(ledger.LedgerAuditPending):
            ledger.execute_batch(_risk_draft(), "ex_no_pending_bypass", **risk_execution_store)
        assert _ledger_state(risk_execution_store)["transactions"] == []
    ledger.set_payment_pause(False, operation_id="op_pending_resume", **risk_execution_store)
    ledger.execute_batch(_risk_draft(), "ex_after_pending_resume", **risk_execution_store)
    assert len(_ledger_state(risk_execution_store)["transactions"]) == 1


def test_enforcement_no_optional_otp_or_client_policy_can_enter_execution(
    risk_execution_store: dict[str, Path],
    risk_policy: rules.TransferPolicy,
) -> None:
    before = _financial_state(risk_execution_store)
    for arguments in (
        {"verified": True},
        {"otp": "123456"},
        {"policy": risk_policy},
        {"skip_risk": True},
    ):
        with pytest.raises(TypeError):
            ledger.execute_batch(
                _risk_draft("8000.00"), "ex_no_override", **arguments, **risk_execution_store
            )
    assert _financial_state(risk_execution_store) == before


@pytest.fixture
def gateway_limits() -> gateway.GatewayPolicy:
    """Explicit prototype resource-policy fixtures, never live defaults."""
    return gateway.GatewayPolicy(
        nonce_lifetime_seconds=300,
        receipt_lifetime_seconds=600,
        max_receipt_bytes=4096,
        max_draft_bytes=32768,
        max_store_bytes=8 * 1024 * 1024,
        max_challenges=32,
        max_challenges_per_user=8,
        max_events=256,
        max_attempts=5,
    )


def _test_jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": gateway._b64(numbers.x.to_bytes(32, "big")),
        "y": gateway._b64(numbers.y.to_bytes(32, "big")),
    }


@pytest.fixture
def gateway_environment(
    risk_execution_store: dict[str, Path],
    gateway_limits: gateway.GatewayPolicy,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> SimpleNamespace:
    """Real J-04/J-07/J-08 and cryptography; only missing trusted providers are fixtures."""
    monkeypatch.setattr(gateway, "gateway_policy", lambda: gateway_limits)
    monkeypatch.setattr(config, "VALIDATION_RECEIPT_KEY", gateway._b64(secrets.token_bytes(32)))
    monkeypatch.setattr(
        readonly, "_ledger_path", lambda path: path or risk_execution_store["storage_path"]
    )
    monkeypatch.setattr(devices, "__file__", str(tmp_path / "app" / "bank" / "devices.py"))
    nonce_path = tmp_path / "nonces.json"
    monkeypatch.setattr(gateway, "_path", lambda path: path or nonce_path)
    audit_lock = audit._locked_file

    @contextmanager
    def isolated_audit(path: Path | None) -> Any:
        with audit_lock(path or risk_execution_store["audit_path"]) as stream:
            yield stream

    monkeypatch.setattr(audit, "_locked_file", isolated_audit)
    private = ec.generate_private_key(ec.SECP256R1())
    devices.register_public_key("u_demo", _test_jwk(private))
    contexts: dict[str, gateway.TrustedContext] = {}
    context_lock = ledger.threading.RLock()

    @contextmanager
    def lease(draft: Draft) -> Any:
        with context_lock:
            context = contexts.get(draft.draft_id)
            if context is None:
                raise gateway.GatewayUnavailable("trusted_context_unavailable")
            yield context

    monkeypatch.setattr(gateway, "trusted_context", lease)

    def approve(
        draft: Draft, logical_id: str | None = None, revision: str = "r_1"
    ) -> gateway.TrustedContext:
        with context_lock:
            context = gateway.TrustedContext(
                user_id=draft.user_id,
                session_id=draft.session_id,
                draft_id=draft.draft_id,
                context_id="ctx_" + secrets.token_hex(16),
                revision=revision,
                logical_id=logical_id or "logical_" + secrets.token_hex(16),
                draft_hash=hashlib.sha256(
                    json.dumps(
                        draft.model_dump(mode="json"),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
                expires_at=gateway._now() + timedelta(seconds=600),
            )
            contexts[draft.draft_id] = context
            return context

    return SimpleNamespace(
        paths={
            "storage_path": nonce_path,
            "ledger_path": risk_execution_store["storage_path"],
            "audit_path": risk_execution_store["audit_path"],
        },
        ledger_paths=risk_execution_store,
        private=private,
        approve=approve,
        contexts=contexts,
        context_lock=context_lock,
        limits=gateway_limits,
        device_root=tmp_path,
    )


def _synthetic_receipt(draft: Draft, context: gateway.TrustedContext, **changes: Any) -> str:
    """Independent synthetic issuer in TEST CODE ONLY. No HTTP route or production issuer."""
    fields = {
        "version": 1,
        "audience": "dcta.transaction.validation",
        **context.model_dump(mode="json"),
        "issued_at": gateway._now().isoformat(),
        **changes,
    }
    claims = gateway.ReceiptClaims.model_validate(fields)
    payload = json.dumps(
        claims.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    key = base64.urlsafe_b64decode(
        config.VALIDATION_RECEIPT_KEY + "=" * (-len(config.VALIDATION_RECEIPT_KEY) % 4)
    )
    mac = hmac.digest(key, b"DCTA-VALIDATION-RECEIPT\0v1\0" + payload, "sha256")
    return "vr_1." + gateway._b64(payload) + "." + gateway._b64(mac)


def _sign_payment(private: ec.EllipticCurvePrivateKey, challenge: Any) -> ExecuteRequest:
    encoded = private.sign(challenge.signing_payload.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(encoded)
    return ExecuteRequest(
        nonce=challenge.nonce, signature=gateway._b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    )


def _gateway_challenge(
    env: SimpleNamespace, draft: Draft | None = None, logical_id: str | None = None
) -> tuple[Draft, Any, ExecuteRequest]:
    draft = draft or _risk_draft("500.00")
    context = env.approve(draft, logical_id)
    challenge = gateway.issue_challenge(
        ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)), **env.paths
    )
    return draft, challenge, _sign_payment(env.private, challenge)


def _gateway_state(env: SimpleNamespace) -> dict[str, Any]:
    return gateway._read(env.paths["storage_path"], True, env.limits)


def test_gateway_valid_receipt_signature_and_actual_signed_500_transfer(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft, challenge, request = _gateway_challenge(env)
    assert json.loads(challenge.signing_payload.rsplit(".", 1)[0]) == draft.model_dump(mode="json")
    assert challenge.signing_payload == gateway.canonical_draft(draft) + "." + challenge.nonce
    assert len(gateway._decode(request.signature)) == 64
    result = gateway.execute(request, **env.paths)
    assert result.status == "executed"
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64500.00"
    assert gateway.execute(request, **env.paths) == result
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    assert _gateway_state(env)["challenges"][challenge.nonce]["status"] == "committed"
    if gateway.os.name != "nt":
        assert env.paths["storage_path"].stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "mutation",
    [
        "amount",
        "payee",
        "currency",
        "user",
        "account",
        "transcript",
        "session",
        "draft_id",
        "actions",
        "created_at",
    ],
)
def test_gateway_receipt_binds_all_draft_fields(
    gateway_environment: SimpleNamespace,
    mutation: str,
) -> None:
    env = gateway_environment
    draft = _risk_draft("500.00")
    receipt = _synthetic_receipt(draft, env.approve(draft))
    data = draft.model_dump(mode="json")
    if mutation == "amount":
        data["actions"][0]["amount"] = "5000.00"
    elif mutation == "payee":
        data["actions"][0]["to_payee_id"] = "pay_8892"
    elif mutation == "currency":
        data["actions"][0]["currency"] = "USD"
    elif mutation == "account":
        data["actions"][0]["from_account_id"] = "acc_1234"
    elif mutation == "user":
        data["user_id"] = "u_other"
    elif mutation == "actions":
        data["actions"].append(_ledger_action("bill_payment"))
    elif mutation == "created_at":
        data["created_at"] = "2026-10-14T01:00:00Z"
    else:
        data["session_id" if mutation == "session" else mutation] = (
            "changed"
            if mutation == "transcript"
            else "d_changed"
            if mutation == "draft_id"
            else "s_changed"
        )
    before = _financial_state(env.ledger_paths)
    with pytest.raises(gateway.GatewayRejected, match="invalid_receipt"):
        gateway.issue_challenge(
            ChallengeRequest(draft=Draft.model_validate(data), receipt=receipt), **env.paths
        )
    assert _financial_state(env.ledger_paths) == before
    assert not env.paths["storage_path"].exists()


@pytest.mark.parametrize(
    "receipt",
    [
        "",
        "vr_stub",
        "vr_1.a.b",
        "vr2.a.b",
        "vr_1.YQ==.Yg==",
        "vr_1.**.**",
        "vr_1.a.b.extra",
        "x" * 4097,
    ],
)
def test_gateway_malformed_or_oversized_receipts_fail_closed(
    gateway_environment: SimpleNamespace,
    receipt: str,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    env.approve(draft)
    with pytest.raises(gateway.GatewayRejected, match="invalid_receipt"):
        gateway.issue_challenge(ChallengeRequest(draft=draft, receipt=receipt), **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", "r_superseded"),
        ("context_id", "ctx_other"),
        ("logical_id", "logical_other"),
        ("draft_hash", "f" * 64),
        ("user_id", "u_other"),
    ],
)
def test_gateway_authenticated_but_mismatched_receipt_context_is_rejected(
    gateway_environment: SimpleNamespace,
    field: str,
    value: str,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    context = env.approve(draft)
    receipt = _synthetic_receipt(draft, context, **{field: value})
    with pytest.raises(gateway.GatewayRejected, match="receipt_superseded|invalid_receipt"):
        gateway.issue_challenge(ChallengeRequest(draft=draft, receipt=receipt), **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


@pytest.mark.parametrize("clock", ["expired", "future", "too_long"])
def test_gateway_receipt_times_are_enforced_by_server(
    gateway_environment: SimpleNamespace,
    clock: str,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    context = env.approve(draft)
    now = gateway._now()
    changes = (
        {"issued_at": now - timedelta(seconds=60), "expires_at": now - timedelta(seconds=1)}
        if clock == "expired"
        else {"issued_at": now + timedelta(seconds=1)}
        if clock == "future"
        else {"issued_at": now - timedelta(seconds=1000)}
    )
    with pytest.raises(gateway.GatewayRejected, match="receipt_expired|invalid_receipt"):
        gateway.issue_challenge(
            ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context, **changes)),
            **env.paths,
        )


@pytest.mark.parametrize("key", ["", "short", "not-a-key!", "YQ=="])
def test_gateway_missing_or_invalid_secret_never_generates_fallback(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    receipt = _synthetic_receipt(draft, env.approve(draft))
    monkeypatch.setattr(config, "VALIDATION_RECEIPT_KEY", key)
    with pytest.raises(gateway.GatewayUnavailable, match="receipt_key_unavailable"):
        gateway.issue_challenge(ChallengeRequest(draft=draft, receipt=receipt), **env.paths)
    assert not env.paths["storage_path"].exists()


def test_gateway_forged_mac_and_cross_domain_mac_are_rejected(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    receipt = _synthetic_receipt(draft, env.approve(draft))
    prefix, body, mac = receipt.split(".")
    for changed in (
        gateway._b64(secrets.token_bytes(32)),
        gateway._b64(
            hmac.digest(
                gateway._secret(), b"DCTA-CHALLENGE-STORE\0v1\0" + gateway._decode(body), "sha256"
            )
        ),
    ):
        assert changed != mac
        with pytest.raises(gateway.GatewayRejected):
            gateway.verify_receipt(draft, prefix + "." + body + "." + changed)


@pytest.mark.parametrize(
    "signature", ["", "!", "=" * 86, "A" * 85, "A" * 87, "A" * 86, "A" * 10000]
)
def test_gateway_strict_raw_signature_format_and_invalid_values(
    gateway_environment: SimpleNamespace,
    signature: str,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env)
    with pytest.raises(gateway.GatewayRejected, match="bad_signature"):
        gateway.execute(request.model_copy(update={"signature": signature}), **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_wrong_key_curve_nonce_and_mutated_multi_action_signatures(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft = _ledger_draft(_ledger_action(), _ledger_action("bill_payment"))
    _, challenge, request = _gateway_challenge(env, draft)
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(gateway.GatewayRejected, match="bad_signature"):
        gateway.execute(_sign_payment(other, challenge), **env.paths)
    altered = copy.deepcopy(draft.model_dump(mode="json"))
    altered["actions"][1]["amount"] = "100.00"
    encoded = env.private.sign(
        (
            json.dumps(altered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "."
            + challenge.nonce
        ).encode(),
        ec.ECDSA(hashes.SHA256()),
    )
    r, s = utils.decode_dss_signature(encoded)
    changed_signature = gateway._b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    with pytest.raises(gateway.GatewayRejected, match="bad_signature"):
        gateway.execute(request.model_copy(update={"signature": changed_signature}), **env.paths)
    with pytest.raises(gateway.GatewayRejected, match="bad_signature"):
        gateway.verify_signature(
            {**_test_jwk(env.private), "crv": "P-384"}, challenge.signing_payload, request.signature
        )
    with pytest.raises(gateway.GatewayRejected, match="unknown_nonce"):
        gateway.execute(
            request.model_copy(update={"nonce": "n_" + secrets.token_urlsafe(32)}), **env.paths
        )
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_challenge_key_binding_rejects_even_valid_signature_for_replaced_key(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    _, challenge, _ = _gateway_challenge(env)
    other = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(devices, "get_public_key", lambda user: _test_jwk(other))
    with pytest.raises(gateway.GatewayRejected, match="bad_signature"):
        gateway.execute(_sign_payment(other, challenge), **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_new_revision_invalidates_previous_nonce_and_receipt(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft, _, request = _gateway_challenge(env)
    previous = env.contexts[draft.draft_id]
    changed = _risk_draft("600.00")
    context = env.approve(changed, previous.logical_id, "r_2")
    challenge = gateway.issue_challenge(
        ChallengeRequest(draft=changed, receipt=_synthetic_receipt(changed, context)), **env.paths
    )
    with pytest.raises(gateway.GatewayRejected, match="nonce_used"):
        gateway.execute(request, **env.paths)
    result = gateway.execute(_sign_payment(env.private, challenge), **env.paths)
    assert result.status == "executed"
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64400.00"


def test_gateway_superseded_or_missing_context_blocks_execution_without_new_challenge(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft, _, request = _gateway_challenge(env)
    context = env.contexts[draft.draft_id]
    env.approve(draft, context.logical_id, "r_new")
    with pytest.raises(gateway.GatewayRejected, match="receipt_superseded"):
        gateway.execute(request, **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []
    other = _risk_draft().model_copy(update={"draft_id": "d_missing"})
    _, _, request = _gateway_challenge(env, other)
    env.contexts.clear()
    with pytest.raises(gateway.GatewayUnavailable, match="trusted_context_unavailable"):
        gateway.execute(request, **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_multiple_challenges_share_execution_identity_and_never_redebit(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft, challenge, request = _gateway_challenge(env)
    context = env.contexts[draft.draft_id]
    second = gateway.issue_challenge(
        ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)), **env.paths
    )
    assert challenge.nonce != second.nonce
    records = _gateway_state(env)["challenges"]
    assert records[challenge.nonce]["execution_id"] == records[second.nonce]["execution_id"]
    gateway.execute(request, **env.paths)
    with pytest.raises(gateway.GatewayRejected, match="nonce_used"):
        gateway.execute(_sign_payment(env.private, second), **env.paths)
    with pytest.raises(gateway.GatewayRejected, match="logical_transaction_committed"):
        gateway.issue_challenge(
            ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)), **env.paths
        )
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


def test_gateway_fresh_customer_request_with_identical_details_is_a_new_transfer(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env)
    gateway.execute(request, **env.paths)
    fresh = _risk_draft("500.00").model_copy(
        update={"draft_id": "d_fresh_request", "session_id": "s_fresh"}
    )
    _, _, second = _gateway_challenge(env, fresh)
    gateway.execute(second, **env.paths)
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64000.00"
    assert len(_ledger_state(env.ledger_paths)["executions"]) == 2


@pytest.mark.parametrize("shared_nonce", [True, False])
def test_gateway_concurrent_thread_replays_and_sibling_challenges_execute_once(
    gateway_environment: SimpleNamespace,
    shared_nonce: bool,
) -> None:
    env = gateway_environment
    draft, _, first = _gateway_challenge(env)
    requests = [first] * 6
    if not shared_nonce:
        context = env.contexts[draft.draft_id]
        requests = [first] + [
            _sign_payment(
                env.private,
                gateway.issue_challenge(
                    ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)),
                    **env.paths,
                ),
            )
            for _ in range(5)
        ]
    barrier = Barrier(6)

    def submit(request: ExecuteRequest) -> Any:
        barrier.wait(timeout=10)
        try:
            return gateway.execute(request, **env.paths).status
        except gateway.GatewayRejected as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(submit, requests))
    assert outcomes.count("executed") == (6 if shared_nonce else 1)
    assert outcomes.count("nonce_used") == (0 if shared_nonce else 5)
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


@pytest.mark.parametrize(
    "amount,status,reason",
    [("50000.00", "rejected", "limit_exceeded"), ("8000.00", "needs_extra_check", None)],
)
def test_gateway_real_risk_hard_limit_and_step_up_never_commit(
    gateway_environment: SimpleNamespace,
    amount: str,
    status: str,
    reason: str | None,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env, _risk_draft(amount))
    before = _financial_state(env.ledger_paths)
    result = gateway.execute(request, **env.paths)
    assert result.status == status
    if reason:
        assert result.reason == reason
    assert _financial_state(env.ledger_paths) == before


def test_gateway_raw_otp_is_never_a_proof_and_attempts_are_bounded(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env, _risk_draft("8000.00"))
    for _ in range(env.limits.max_attempts - 1):
        assert (
            gateway.execute(request.model_copy(update={"otp": "123456"}), **env.paths).status
            == "needs_extra_check"
        )
    assert (
        gateway.execute(request.model_copy(update={"otp": "123456"}), **env.paths).reason
        == "bad_otp"
    )
    with pytest.raises(gateway.GatewayRejected, match="nonce_used"):
        gateway.execute(request, **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []
    assert "123456" not in env.paths["storage_path"].read_text()
    assert "123456" not in env.paths["audit_path"].read_text()


@pytest.mark.parametrize("block", ["pause", "policy", "bill", "stock", "fx", "kyc", "funds"])
def test_gateway_preserves_ledger_policy_pause_and_financial_checks(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    block: str,
) -> None:
    env = gateway_environment
    if block == "pause":
        ledger.set_payment_pause(True, operation_id="op_gateway_pause", **env.ledger_paths)
    if block == "policy":
        monkeypatch.setattr(rules, "execution_policy", lambda: None)
    draft = (
        _ledger_draft(
            _ledger_action(
                {"bill": "bill_payment", "stock": "stock_order", "fx": "fx_conversion"}[block]
            )
        )
        if block in {"bill", "stock", "fx"}
        else _risk_draft()
    )
    if block in {"kyc", "funds"}:
        state = _ledger_state(env.ledger_paths)
        if block == "kyc":
            state["users"]["u_demo"]["kyc"]["status"] = "pending"
        else:
            state["users"]["u_demo"]["accounts"][1]["balance"] = "0.00"
        ledger._save_state(env.ledger_paths["storage_path"], state)
    _, _, request = _gateway_challenge(env, draft)
    before = _financial_state(env.ledger_paths)
    if block in {"kyc", "funds"}:
        assert gateway.execute(request, **env.paths).reason == (
            "kyc_incomplete" if block == "kyc" else "insufficient_funds"
        )
    else:
        with pytest.raises(
            gateway.GatewayUnavailable, match="payments_paused|policy_missing|policy_unsupported"
        ):
            gateway.execute(request, **env.paths)
    assert _financial_state(env.ledger_paths) == before


def test_gateway_api_has_no_fake_success_or_public_bypass_and_preserves_contracts(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft = _risk_draft("500.00")
    context = env.approve(draft)
    client = TestClient(app)
    reply = client.post(
        "/api/challenge",
        json={
            "draft": draft.model_dump(mode="json"),
            "receipt": _synthetic_receipt(draft, context),
        },
    )
    assert reply.status_code == 200
    assert set(reply.json()) == {"nonce", "signing_payload", "expires_at"}
    challenge = gateway.ChallengeResponse.model_validate(reply.json())
    signed = _sign_payment(env.private, challenge)
    response = client.post("/api/execute", json=signed.model_dump(mode="json"))
    assert response.json() == {
        "status": "executed",
        "results": [{"action_index": 0, "reference": "tx_00000001"}],
    }
    assert (
        client.post("/api/execute", json=signed.model_dump(mode="json")).json() == response.json()
    )
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64500.00"
    for extra in (
        {"validation_passed": True},
        {"verified": True},
        {"public_key_jwk": _test_jwk(env.private)},
        {"skip_risk": True},
    ):
        assert (
            client.post(
                "/api/execute", json={**signed.model_dump(mode="json"), **extra}
            ).status_code
            == 400
        )


def test_gateway_live_defaults_fail_closed_without_touching_real_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gateway, "_path", lambda path: tmp_path / "never-created.json")
    assert gateway.gateway_policy() is None
    client = TestClient(app)
    request = {"nonce": "n_" + secrets.token_urlsafe(32), "signature": "fake", "otp": None}
    reply = client.post("/api/execute", json=request)
    assert reply.status_code == 503 and reply.json()["error"]["code"] == "upstream_error"
    assert "executed" not in reply.text
    assert not [p for p in tmp_path.iterdir() if p.name != "evidence"]


@pytest.mark.parametrize(
    "gap", ["before_commit", "after_commit", "after_gateway_save", "audit_before_ack"]
)
def test_gateway_crash_gaps_recover_original_identity_and_single_debit(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    gap: str,
) -> None:
    env = gateway_environment
    draft, challenge, request = _gateway_challenge(env)
    commit = gateway._commit
    save = gateway._save
    append = audit.append_event

    def stopped_commit(*args: Any, **kwargs: Any) -> Any:
        if gap == "after_commit":
            commit(*args, **kwargs)
        raise gateway.GatewayUnavailable("simulated_crash")

    def stopped_save(path: Path, state: dict[str, Any], policy: gateway.GatewayPolicy) -> None:
        record = state["challenges"][challenge.nonce]
        if record["status"] == "committed":
            save(path, state, policy)
            raise gateway.GatewayUnavailable("simulated_crash")
        save(path, state, policy)

    def stopped_append(actor: str, event: str, data: dict[str, Any], **kwargs: Any) -> Any:
        result = append(actor, event, data, **kwargs)
        if event == "gateway_execution_confirmed":
            raise audit.AuditError("simulated_crash_after_append")
        return result

    with monkeypatch.context() as patch:
        if gap in {"before_commit", "after_commit"}:
            patch.setattr(gateway, "_commit", stopped_commit)
        elif gap == "after_gateway_save":
            patch.setattr(gateway, "_save", stopped_save)
        else:
            patch.setattr(audit, "append_event", stopped_append)
        with pytest.raises(gateway.GatewayUnavailable):
            gateway.execute(request, **env.paths)
    count = 0 if gap == "before_commit" else 1
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == count
    result = gateway.execute(request, **env.paths)
    assert result.status == "executed"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64500.00"
    log = audit.read_events(100, storage_path=env.paths["audit_path"])
    assert log.chain_valid
    assert sum(e.event == "transaction_executed" for e in log.entries) == 1
    assert sum(e.event == "gateway_execution_confirmed" for e in log.entries) <= 1
    assert _gateway_state(env)["outbox"] == []
    assert (
        _gateway_state(env)["challenges"][request.nonce]["execution_id"]
        in _ledger_state(env.ledger_paths)["executions"]
    )


@pytest.mark.parametrize(
    "gap", ["signature_audit", "start_audit", "ledger_audit", "confirmation_audit"]
)
def test_gateway_audit_failures_never_fabricate_success_and_recover(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    gap: str,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env)
    append = audit.append_event
    blocked = {
        "signature_audit": "signature_invalid",
        "start_audit": "gateway_execution_started",
        "ledger_audit": "transaction_executed",
        "confirmation_audit": "gateway_execution_confirmed",
    }[gap]

    def unavailable(actor: str, event: str, data: dict[str, Any], **kwargs: Any) -> Any:
        if event == blocked:
            raise audit.AuditError("Unavailable")
        return append(actor, event, data, **kwargs)

    attempt = (
        request.model_copy(update={"signature": "fake"}) if gap == "signature_audit" else request
    )
    with monkeypatch.context() as patch:
        patch.setattr(audit, "append_event", unavailable)
        with pytest.raises(gateway.GatewayUnavailable):
            gateway.execute(attempt, **env.paths)
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == (
        1 if gap in {"ledger_audit", "confirmation_audit"} else 0
    )
    assert gateway.execute(request, **env.paths).status == "executed"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    assert audit.read_events(100, storage_path=env.paths["audit_path"]).chain_valid


@pytest.mark.parametrize("stage", ["inflight", "confirmation"])
def test_gateway_atomic_write_failure_retains_safe_recovery_state(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    env = gateway_environment
    _, challenge, request = _gateway_challenge(env)
    save = gateway._save

    def unavailable(path: Path, state: dict[str, Any], policy: gateway.GatewayPolicy) -> None:
        if state["challenges"][challenge.nonce]["status"] == (
            "inflight" if stage == "inflight" else "committed"
        ):
            raise gateway.GatewayUnavailable("challenge_storage_unavailable")
        save(path, state, policy)

    with monkeypatch.context() as patch:
        patch.setattr(gateway, "_save", unavailable)
        with pytest.raises(gateway.GatewayUnavailable):
            gateway.execute(request, **env.paths)
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == (0 if stage == "inflight" else 1)
    assert gateway.execute(request, **env.paths).status == "executed"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


@pytest.mark.parametrize("expired", ["nonce", "receipt"])
def test_gateway_expired_uncommitted_approval_never_moves_money(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    expired: str,
) -> None:
    env = gateway_environment
    _, challenge, request = _gateway_challenge(env)
    later = challenge.expires_at + timedelta(seconds=1 if expired == "nonce" else 400)
    monkeypatch.setattr(gateway, "_now", lambda: later)
    with pytest.raises(gateway.GatewayRejected, match="nonce_expired"):
        gateway.execute(request, **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_expired_committed_exact_retry_only_recovers_actual_evidence(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    _, challenge, request = _gateway_challenge(env)
    first = gateway.execute(request, **env.paths)
    monkeypatch.setattr(gateway, "_now", lambda: challenge.expires_at + timedelta(days=1))
    env.contexts.clear()
    monkeypatch.setattr(rules, "execution_policy", lambda: None)
    ledger.set_payment_pause(True, operation_id="op_retry_paused", **env.ledger_paths)
    assert gateway.execute(request, **env.paths) == first
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    with pytest.raises(gateway.GatewayRejected, match="nonce_used"):
        gateway.execute(_sign_payment(env.private, challenge), **env.paths)


@pytest.mark.parametrize("storage", ["challenge", "ledger", "audit", "device"])
def test_gateway_corrupted_storage_fails_closed_without_financial_changes(
    gateway_environment: SimpleNamespace,
    storage: str,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env)
    paths = {
        "challenge": env.paths["storage_path"],
        "ledger": env.paths["ledger_path"],
        "audit": env.paths["audit_path"],
        "device": env.device_root / "var" / "devices.json",
    }
    paths[storage].write_text("corrupted\n")
    original = env.paths["ledger_path"].read_bytes()
    with pytest.raises((gateway.GatewayUnavailable, devices.DeviceStorageError)):
        gateway.execute(request, **env.paths)
    assert env.paths["ledger_path"].read_bytes() == original


@pytest.mark.parametrize("tamper", ["amount", "status", "key", "nonce", "result", "outbox"])
def test_gateway_authenticated_store_refuses_tampering_without_reset(
    gateway_environment: SimpleNamespace,
    tamper: str,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env)
    state = json.loads(env.paths["storage_path"].read_text())
    record = state["challenges"][request.nonce]
    if tamper == "amount":
        record["draft"]["actions"][0]["amount"] = "5000.00"
    elif tamper == "status":
        record["status"] = "committed"
    elif tamper == "key":
        record["key_hash"] = "f" * 64
    elif tamper == "nonce":
        state["challenges"]["n_" + secrets.token_urlsafe(32)] = state["challenges"].pop(
            request.nonce
        )
    elif tamper == "result":
        record["result"] = {
            "status": "executed",
            "results": [{"action_index": 0, "reference": "forged"}],
        }
    else:
        state["outbox"] = ["unknown"]
    env.paths["storage_path"].write_text(json.dumps(state) + "\n")
    original = env.paths["storage_path"].read_bytes()
    with pytest.raises(gateway.GatewayUnavailable, match="challenge_storage_unavailable"):
        gateway.execute(request, **env.paths)
    assert env.paths["storage_path"].read_bytes() == original
    assert _ledger_state(env.ledger_paths)["transactions"] == []


@pytest.mark.parametrize("missing", ["state", "both", "audit_evidence"])
def test_gateway_missing_established_state_or_evidence_is_not_reset(
    gateway_environment: SimpleNamespace,
    missing: str,
) -> None:
    env = gateway_environment
    draft, _, request = _gateway_challenge(env)
    if missing == "audit_evidence":
        env.paths["audit_path"].write_text("")
    else:
        env.paths["storage_path"].unlink()
        if missing == "both":
            env.paths["storage_path"].with_suffix(".lock").unlink()
    with pytest.raises(gateway.GatewayUnavailable):
        gateway.execute(request, **env.paths)
    if missing != "audit_evidence":
        assert not env.paths["storage_path"].exists()
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_storage_cleanup_is_bounded_but_never_discards_uncertain_execution(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    draft, _, request = _gateway_challenge(env)
    original = gateway._commit

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise gateway.GatewayUnavailable("uncertain")

    with monkeypatch.context() as patch:
        patch.setattr(gateway, "_commit", unavailable)
        with pytest.raises(gateway.GatewayUnavailable):
            gateway.execute(request, **env.paths)
    assert _gateway_state(env)["challenges"][request.nonce]["status"] == "inflight"
    later = gateway._now() + timedelta(days=1)
    monkeypatch.setattr(gateway, "_now", lambda: later)
    fresh = _risk_draft().model_copy(update={"draft_id": "d_cleanup"})
    env.approve(fresh)
    gateway.issue_challenge(
        ChallengeRequest(
            draft=fresh, receipt=_synthetic_receipt(fresh, env.contexts[fresh.draft_id])
        ),
        **env.paths,
    )
    assert request.nonce in _gateway_state(env)["challenges"]
    assert gateway._commit is original
    with pytest.raises(gateway.GatewayRejected, match="nonce_expired"):
        gateway.execute(request, **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_capacity_and_signature_attempt_limits_are_enforced(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    draft, _, request = _gateway_challenge(env)
    bad = request.model_copy(update={"signature": "bad"})
    for _ in range(env.limits.max_attempts):
        with pytest.raises(gateway.GatewayRejected, match="bad_signature"):
            gateway.execute(bad, **env.paths)
    with pytest.raises(gateway.GatewayRejected, match="nonce_used"):
        gateway.execute(request, **env.paths)
    context = env.contexts[draft.draft_id]
    for _ in range(env.limits.max_challenges_per_user - 1):
        gateway.issue_challenge(
            ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)), **env.paths
        )
    with pytest.raises(gateway.GatewayUnavailable, match="challenge_capacity_exceeded"):
        gateway.issue_challenge(
            ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)), **env.paths
        )
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_step_up_attempts_cannot_be_reset_by_another_challenge(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft, _, first = _gateway_challenge(env, _risk_draft("8000.00"))
    context = env.contexts[draft.draft_id]
    second = _sign_payment(
        env.private,
        gateway.issue_challenge(
            ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)), **env.paths
        ),
    )
    for _ in range(3):
        gateway.execute(first.model_copy(update={"otp": "fictional"}), **env.paths)
    assert (
        gateway.execute(second.model_copy(update={"otp": "fictional"}), **env.paths).status
        == "needs_extra_check"
    )
    assert (
        gateway.execute(second.model_copy(update={"otp": "fictional"}), **env.paths).reason
        == "bad_otp"
    )
    with pytest.raises(gateway.GatewayRejected, match="step_up_locked"):
        gateway.issue_challenge(
            ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)), **env.paths
        )
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_no_transcripts_otps_keys_or_money_in_security_audit(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft = _risk_draft("8000.00").model_copy(
        update={"transcript": "PRIVATE_FICTIONAL_INSTRUCTION_879123"}
    )
    _, _, request = _gateway_challenge(env, draft)
    gateway.execute(request.model_copy(update={"otp": "879123"}), **env.paths)
    events = audit.read_events(1000, storage_path=env.paths["audit_path"])
    text = events.model_dump_json()
    for forbidden in (
        "PRIVATE_FICTIONAL",
        "879123",
        config.VALIDATION_RECEIPT_KEY,
        "8000.00",
        "acc_5678",
        "u_demo",
        "John Doe",
    ):
        assert forbidden not in text
    assert "private_key" not in text


def test_gateway_context_lease_prevents_correction_racing_with_execution(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    draft, _, request = _gateway_challenge(env)
    entered, proceed, correction_started, correction_finished = Event(), Event(), Event(), Event()
    commit = gateway._commit

    def slow_commit(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert proceed.wait(timeout=10)
        assert not correction_finished.is_set()
        return commit(*args, **kwargs)

    def correction() -> None:
        correction_started.set()
        env.approve(draft, env.contexts[draft.draft_id].logical_id, "r_new")
        correction_finished.set()

    monkeypatch.setattr(gateway, "_commit", slow_commit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(gateway.execute, request, **env.paths)
        assert entered.wait(timeout=10)
        update = pool.submit(correction)
        assert correction_started.wait(timeout=10)
        assert not correction_finished.is_set()
        proceed.set()
        assert execution.result(timeout=10).status == "executed"
        update.result(timeout=10)
    assert correction_finished.is_set()
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


_GATEWAY_PROCESS = """
import json,sys
from contextlib import contextmanager
from pathlib import Path
from app import config
from app.bank import gateway,devices,readonly,rules
from app.contracts import Draft,ExecuteRequest
# Synthetic trusted fixtures arrive on stdin, never argv or source literals.
body=json.load(sys.stdin)
config.VALIDATION_RECEIPT_KEY=body["key"]
policy=gateway.GatewayPolicy.model_validate(body["policy"])
gateway.gateway_policy=lambda: policy
risk=rules.TransferPolicy.model_validate(body["risk"])
rules.execution_policy=lambda: risk
paths={k:Path(v) for k,v in body["paths"].items()}
readonly._ledger_path=lambda path: path or paths["ledger_path"]
devices.__file__=body["device_file"]
context=gateway.TrustedContext.model_validate(body["context"])
@contextmanager
def lease(draft):
    yield context
gateway.trusted_context=lease
request=ExecuteRequest.model_validate(body["request"])
"""


def _gateway_process_body(env: SimpleNamespace, draft: Draft, request: ExecuteRequest) -> str:
    return json.dumps(
        {
            "key": config.VALIDATION_RECEIPT_KEY,
            "policy": env.limits.model_dump(mode="json"),
            "risk": rules.execution_policy().model_dump(mode="json"),
            "paths": {k: str(v) for k, v in env.paths.items()},
            "device_file": str(env.device_root / "app" / "bank" / "devices.py"),
            "context": env.contexts[draft.draft_id].model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
        }
    )


@pytest.mark.parametrize("siblings", [True, False])
def test_gateway_separate_process_replays_and_multiple_challenges_debit_once(
    gateway_environment: SimpleNamespace,
    siblings: bool,
) -> None:
    env = gateway_environment
    draft, _, request = _gateway_challenge(env)
    requests = [request] * 4
    if siblings:
        context = env.contexts[draft.draft_id]
        requests = [request] + [
            _sign_payment(
                env.private,
                gateway.issue_challenge(
                    ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context)),
                    **env.paths,
                ),
            )
            for _ in range(3)
        ]
    script = (
        _GATEWAY_PROCESS
        + """
try:
    result=gateway.execute(request,**paths)
    sys.stdout.write(result.status)
except gateway.GatewayRejected as error:
    sys.stdout.write(str(error))
"""
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in requests
    ]
    try:
        for process, payment in zip(processes, requests, strict=True):
            process.stdin.write(_gateway_process_body(env, draft, payment))
            process.stdin.close()
            process.stdin = None
        outputs = [process.communicate(timeout=30) for process in processes]
        assert all(process.returncode == 0 for process in processes), [err for _, err in outputs]
        outcomes = [out for out, _ in outputs]
        assert outcomes.count("executed") == (1 if siblings else 4)
        assert outcomes.count("nonce_used") == (3 if siblings else 0)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64500.00"


@pytest.mark.parametrize(
    "gap",
    ["before_commit", "after_ledger_replace", "after_commit", "after_gateway_save", "audit_ack"],
)
def test_gateway_real_process_exit_recovers_single_execution_and_audit_evidence(
    gateway_environment: SimpleNamespace,
    gap: str,
) -> None:
    env = gateway_environment
    draft, challenge, request = _gateway_challenge(env)
    script = (
        _GATEWAY_PROCESS
        + """
import os
from app.bank import ledger
stage=sys.argv[1]
commit=gateway._commit
save=gateway._save
append=gateway.audit.append_event
if stage=="before_commit":
    gateway._commit=lambda *args,**kwargs: os._exit(79)
elif stage=="after_ledger_replace":
    ledger._sync_directory=lambda path: os._exit(79)
elif stage=="after_commit":
    def interrupted_commit(*args,**kwargs):
        commit(*args,**kwargs)
        os._exit(79)
    gateway._commit=interrupted_commit
elif stage=="after_gateway_save":
    def interrupted_save(path,state,policy):
        save(path,state,policy)
        if state["challenges"][request.nonce]["status"]=="committed":
            os._exit(79)
    gateway._save=interrupted_save
else:
    def interrupted_append(actor,event,data,**kwargs):
        result=append(actor,event,data,**kwargs)
        if event=="gateway_execution_confirmed": os._exit(79)
        return result
    gateway.audit.append_event=interrupted_append
gateway.execute(request,**paths)
"""
    )
    outcome = subprocess.run(
        [sys.executable, "-c", script, gap],
        input=_gateway_process_body(env, draft, request),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert outcome.returncode == 79, outcome.stderr
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == (
        0 if gap == "before_commit" else 1
    )
    if gap != "before_commit":
        # A committed recovery retrieves actual evidence even when later policy/pause
        # would forbid a new payment. J-08 still reconciles its durable execution audit.
        ledger.set_payment_pause(True, operation_id="op_after_gateway_crash", **env.ledger_paths)
        env.contexts.clear()
    result = gateway.execute(request, **env.paths)
    assert result.status == "executed"
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64500.00"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    log = audit.read_events(100, storage_path=env.paths["audit_path"])
    assert log.chain_valid
    assert sum(e.event == "transaction_executed" for e in log.entries) == 1
    assert _gateway_state(env)["outbox"] == []
    assert _gateway_state(env)["challenges"][challenge.nonce]["status"] == "committed"


def test_gateway_persistence_survives_module_reload_and_matches_original_result(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    draft, challenge, request = _gateway_challenge(env)
    first = gateway.execute(request, **env.paths)
    lease = gateway.trusted_context
    importlib.reload(gateway)
    monkeypatch.setattr(gateway, "gateway_policy", lambda: env.limits)
    monkeypatch.setattr(gateway, "trusted_context", lease)
    assert gateway.execute(request, **env.paths) == first
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


def test_gateway_missing_trusted_provider_and_customer_binding_never_issue_approval(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    receipt = _synthetic_receipt(draft, env.approve(draft))

    @contextmanager
    def unavailable(draft: Draft) -> Any:
        raise gateway.GatewayUnavailable("trusted_context_unavailable")
        yield

    monkeypatch.setattr(gateway, "trusted_context", unavailable)
    client = TestClient(app)
    reply = client.post(
        "/api/challenge", json={"draft": draft.model_dump(mode="json"), "receipt": receipt}
    )
    assert reply.status_code == 503 and "signing_payload" not in reply.text
    assert not _gateway_state(env)["challenges"]
    assert _ledger_state(env.ledger_paths)["transactions"] == []


@pytest.mark.parametrize("invalid", ["zero", "extra", "too_long", "inconsistent"])
def test_gateway_requires_valid_explicit_backend_resource_configuration(
    gateway_limits: gateway.GatewayPolicy,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    data = gateway_limits.model_dump(mode="python")
    if invalid == "zero":
        data["max_attempts"] = 0
    elif invalid == "extra":
        data["skip_checks"] = True
    elif invalid == "too_long":
        data["nonce_lifetime_seconds"] = 301
    else:
        data["max_challenges_per_user"] = 1000
    forged = gateway_limits.model_copy(update=data)
    monkeypatch.setattr(gateway, "gateway_policy", lambda: forged)
    if invalid == "extra":
        with pytest.raises(ValidationError):
            gateway.GatewayPolicy.model_validate(data)
    else:
        with pytest.raises(gateway.GatewayUnavailable):
            gateway._policy()


def test_gateway_canonical_signing_includes_unicode_and_two_decimal_money(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    draft = _risk_draft("500.00").model_copy(update={"transcript": "Fictional transfer 五百 林"})
    _, challenge, request = _gateway_challenge(env, draft)
    assert "五百" in challenge.signing_payload and '"amount":"500.00"' in challenge.signing_payload
    assert gateway.execute(request, **env.paths).status == "executed"
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64500.00"


@pytest.mark.parametrize(
    "amount,workers,allowed,reason",
    [("5000.00", 6, 4, "limit_exceeded"), ("100.00", 8, 5, "velocity_exceeded")],
)
def test_gateway_distinct_concurrent_requests_cannot_bypass_daily_or_velocity_limits(
    gateway_environment: SimpleNamespace,
    amount: str,
    workers: int,
    allowed: int,
    reason: str,
) -> None:
    env = gateway_environment
    requests = []
    for index in range(workers):
        draft = _risk_draft(amount).model_copy(update={"draft_id": f"d_atomic_{index}"})
        _, _, request = _gateway_challenge(env, draft)
        requests.append(request)
    barrier = Barrier(workers)

    def submit(request: ExecuteRequest) -> str:
        barrier.wait(timeout=10)
        result = gateway.execute(request, **env.paths)
        return "executed" if result.status == "executed" else result.reason

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(submit, requests))
    assert results.count("executed") == allowed
    assert results.count(reason) == workers - allowed
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == allowed
    assert (
        Decimal(_ledger_balance(env.ledger_paths, "acc_5678"))
        == Decimal("65000.00") - Decimal(amount) * allowed
    )


@pytest.mark.parametrize("mutation", ["amount", "recipient", "currency", "account", "action_list"])
def test_gateway_customer_signature_cannot_cover_altered_signed_transaction(
    gateway_environment: SimpleNamespace,
    mutation: str,
) -> None:
    env = gateway_environment
    draft, challenge, request = _gateway_challenge(env)
    body = draft.model_dump(mode="json")
    if mutation == "amount":
        body["actions"][0]["amount"] = "5000.00"
    elif mutation == "recipient":
        body["actions"][0]["to_payee_id"] = "pay_8892"
    elif mutation == "currency":
        body["actions"][0]["currency"] = "USD"
    elif mutation == "account":
        body["actions"][0]["from_account_id"] = "acc_1234"
    else:
        body["actions"].append(_ledger_action("bill_payment"))
    changed_payload = (
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "."
        + challenge.nonce
    )
    changed = _sign_payment(
        env.private, SimpleNamespace(nonce=challenge.nonce, signing_payload=changed_payload)
    )
    before = _financial_state(env.ledger_paths)
    with pytest.raises(gateway.GatewayRejected, match="bad_signature"):
        gateway.execute(changed, **env.paths)
    assert _financial_state(env.ledger_paths) == before
    assert gateway.execute(request, **env.paths).status == "executed"
    assert _ledger_balance(env.ledger_paths, "acc_5678") == "64500.00"


@pytest.mark.parametrize("malformation", ["duplicate", "noncanonical", "bool_version", "extra"])
def test_gateway_authenticated_malformed_receipt_envelopes_are_strictly_rejected(
    gateway_environment: SimpleNamespace,
    malformation: str,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    receipt = _synthetic_receipt(draft, env.approve(draft))
    raw = gateway._decode(receipt.split(".")[1])
    fields = json.loads(raw)
    if malformation == "duplicate":
        payload = b'{"version":1,' + raw[1:]
    elif malformation == "noncanonical":
        payload = json.dumps(fields, indent=2).encode()
    else:
        if malformation == "bool_version":
            fields["version"] = True
        else:
            fields["validation_passed"] = True
        payload = json.dumps(
            fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    mac = hmac.digest(gateway._secret(), b"DCTA-VALIDATION-RECEIPT\0v1\0" + payload, "sha256")
    with pytest.raises(gateway.GatewayRejected, match="invalid_receipt"):
        gateway.verify_receipt(draft, "vr_1." + gateway._b64(payload) + "." + gateway._b64(mac))


def test_gateway_expiry_is_checked_after_waiting_for_trusted_context_lease(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    draft, challenge, request = _gateway_challenge(env)
    now = gateway._now()
    monkeypatch.setattr(gateway, "_now", lambda: now)
    context = env.contexts[draft.draft_id]

    @contextmanager
    def delayed(draft: Draft) -> Any:
        monkeypatch.setattr(gateway, "_now", lambda: challenge.expires_at + timedelta(seconds=1))
        yield context

    monkeypatch.setattr(gateway, "trusted_context", delayed)
    with pytest.raises(gateway.GatewayRejected, match="nonce_expired"):
        gateway.execute(request, **env.paths)
    assert _ledger_state(env.ledger_paths)["transactions"] == []


def test_gateway_stable_logical_identity_survives_receipt_key_change(
    gateway_environment: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = gateway_environment
    draft = _risk_draft()
    receipt = _synthetic_receipt(draft, env.approve(draft))
    claims = gateway.verify_receipt(draft, receipt)
    identity = gateway._execution_id(claims)
    monkeypatch.setattr(config, "VALIDATION_RECEIPT_KEY", gateway._b64(secrets.token_bytes(32)))
    assert gateway._execution_id(claims) == identity
    with pytest.raises(gateway.GatewayRejected, match="invalid_receipt"):
        gateway.verify_receipt(draft, receipt)


# Integration uses real providers and persistent approvals; only the independent
# model verdict is controlled, so this tests the boundary rather than model quality.
_integrated_context_provider = gateway.trusted_context
_integrated_gateway_policy = gateway.gateway_policy
_integrated_risk_policy = rules.execution_policy


@pytest.fixture
def combined_bank(gateway_environment, risk_policy, monkeypatch, tmp_path):
    from app import transaction_state

    env = gateway_environment
    monkeypatch.setattr(config, "BANK_EXECUTION_ENABLED", True)
    monkeypatch.setattr(config, "BANK_SUPERVISED_USER", "u_demo")
    monkeypatch.setattr(config, "BANK_GATEWAY_POLICY", env.limits.model_dump_json())
    monkeypatch.setattr(config, "BANK_TRANSFER_POLICY", risk_policy.model_dump_json())
    monkeypatch.setattr(config, "TRANSACTION_STATE_PATH", str(tmp_path / "approvals.sqlite3"))
    monkeypatch.setattr(gateway, "trusted_context", _integrated_context_provider)
    monkeypatch.setattr(gateway, "gateway_policy", _integrated_gateway_policy)
    monkeypatch.setattr(rules, "execution_policy", _integrated_risk_policy)
    transaction_state.clear()
    return env


def _validated_payment(env):
    from app import transaction_state

    draft = _risk_draft("50.00")
    transaction_state.register(draft, [{"text": "Send SGD 50 to John"}], {"source": "bank"})
    record, _ = transaction_state.begin(draft)
    receipt = transaction_state.finish(record, {"status": "match"})["receipt"]
    challenge = gateway.issue_challenge(ChallengeRequest(draft=draft, receipt=receipt), **env.paths)
    return draft, _sign_payment(env.private, challenge)


def test_combined_persistent_validation_execution_and_encrypted_timeline(combined_bank):
    from app import evidence, transaction_state

    env = combined_bank
    draft, request = _validated_payment(env)
    # Drop process caches, leaving only persisted authoritative approval state.
    transaction_state._records.clear()
    transaction_state._turns.clear()
    result = gateway.execute(request, **env.paths)
    assert result.status == "executed"
    assert gateway.execute(request, **env.paths) == result
    state = _ledger_state(env.ledger_paths)
    assert len(state["transactions"]) == 1
    assert next(
        a
        for a in readonly.get_accounts("u_demo").accounts
        if a.account_id == draft.actions[0].from_account_id
    ).balance == Decimal("64950.00")
    rows = evidence.timeline(draft.session_id, 100, True)["entries"]
    commits = [r for r in rows if r["event"] == "bank_transaction_committed"]
    assert len(commits) == 1
    assert evidence._signed(
        "draft-digest", transaction_state.digest(draft).encode(), commits[0]["draft_digest"]
    )
    assert commits[0]["evidence"]["result"] == result.model_dump(mode="json")
    assert b"Send SGD 50 to John" not in Path(config.TRANSACTION_STATE_PATH).read_bytes()
    assert b"Send SGD 50 to John" not in Path(config.AUDIT_EVIDENCE_PATH).read_bytes()


def test_combined_cancellation_blocks_existing_signed_approval(combined_bank):
    from app import transaction_state

    draft, request = _validated_payment(combined_bank)
    transaction_state.cancel(draft)
    with pytest.raises(gateway.GatewayRejected):
        gateway.execute(request, **combined_bank.paths)
    assert not _ledger_state(combined_bank.ledger_paths)["transactions"]


def test_combined_evidence_failure_recovers_same_payment(combined_bank, monkeypatch):
    from app import evidence

    env = combined_bank
    draft, request = _validated_payment(env)
    original = evidence.record

    def fail(event, *args, **kwargs):
        if event == "bank_transaction_committed":
            raise audit.AuditError("Interrupted encrypted evidence delivery")
        return original(event, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(evidence, "record", fail)
        with pytest.raises(gateway.GatewayUnavailable):
            gateway.execute(request, **env.paths)
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    assert gateway.execute(request, **env.paths).status == "executed"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    assert (
        len(
            [
                r
                for r in evidence.timeline(draft.session_id, 100, True)["entries"]
                if r["event"] == "bank_transaction_committed"
            ]
        )
        == 1
    )


def test_combined_failed_revocation_audit_still_revokes(combined_bank, monkeypatch):
    from app import evidence, transaction_state

    draft, request = _validated_payment(combined_bank)
    original = evidence.record

    def fail(event, *args, **kwargs):
        if event == "draft_revoked":
            raise audit.AuditError("Interrupted revocation evidence")
        return original(event, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(evidence, "record", fail)
        with pytest.raises(audit.AuditError):
            transaction_state.cancel(draft)
    with pytest.raises(gateway.GatewayRejected):
        gateway.execute(request, **combined_bank.paths)
    assert not _ledger_state(combined_bank.ledger_paths)["transactions"]


def test_combined_user_profile_supplies_limits(combined_bank, monkeypatch):
    monkeypatch.setattr(config, "BANK_TRANSFER_POLICY", "")
    policy = rules.execution_policy()
    assert policy.per_transfer_max == Decimal("25000.00")
    assert policy.daily_transfer_max == Decimal("100000.00")
    assert policy.step_up_above == Decimal("20000.00")
    assert policy.velocity_max_transfers == 10
    _, request = _validated_payment(combined_bank)
    assert gateway.execute(request, **combined_bank.paths).status == "executed"


def test_combined_http_validation_sign_execute(combined_bank, monkeypatch):
    from app import evidence, transaction_state
    from app.validation import validator
    from tests.test_validation import report

    env = combined_bank
    draft = _risk_draft("50.00")
    draft.transcript = "Transfer SGD 50 from Current to John Doe."
    client = TestClient(app)
    client.get("/api/accounts?user_id=u_demo")
    owner = transaction_state.client.set(client.cookies.get("dcta_client"))
    transaction_state.register(
        draft,
        [{"text": draft.transcript}],
        {"accounts": readonly.get_accounts(draft.user_id).model_dump(mode="json")},
    )

    transaction_state.client.reset(owner)

    async def matched(*args):
        return report(draft)

    monkeypatch.setattr(validator.llm, "audit", matched)
    approved = client.post("/api/validate", json={"draft": draft.model_dump(mode="json")})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "match", approved.text
    challenge = client.post(
        "/api/challenge",
        json={"draft": draft.model_dump(mode="json"), "receipt": approved.json()["receipt"]},
    )
    assert challenge.status_code == 200, challenge.text
    request = _sign_payment(env.private, gateway.ChallengeResponse.model_validate(challenge.json()))
    first = client.post("/api/execute", json=request.model_dump(mode="json"))
    assert first.status_code == 200 and first.json()["status"] == "executed", first.text
    assert client.post("/api/execute", json=request.model_dump(mode="json")).json() == first.json()
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1
    rows = evidence.timeline(draft.session_id, 100, True)["entries"]
    commit = next(r for r in rows if r["event"] == "bank_transaction_committed")
    assert commit["revision"]


def test_combined_context_lease_coordinates_another_process(combined_bank):
    from app import transaction_state

    draft, _ = _validated_payment(combined_bank)
    script = """
import json,sys
from app import config,transaction_state
from app.contracts import Draft
body=json.load(sys.stdin)
for key,value in body['config'].items(): setattr(config,key,value)
print('ready',flush=True)
try:
    with transaction_state.execution_context(Draft.model_validate(body['draft'])):
        print('authorized',flush=True)
except transaction_state.InvalidDraft:
    print('revoked',flush=True)
"""
    body = {
        "draft": draft.model_dump(mode="json"),
        "config": {
            key: getattr(config, key)
            for key in [
                "BANK_EXECUTION_ENABLED",
                "BANK_SUPERVISED_USER",
                "TRANSACTION_STATE_PATH",
                "AUDIT_EVIDENCE_KEY",
                "AUDIT_EVIDENCE_PATH",
            ]
        },
    }
    with transaction_state.execution_context(draft):
        process = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process.stdin.write(json.dumps(body))
        process.stdin.close()
        assert process.stdout.readline().strip() == "ready"
        # The child cannot observe or authorize the revision while this lease is held.
        assert process.poll() is None
        transaction_state.cancel(draft)
    try:
        assert process.wait(timeout=15) == 0, process.stderr.read()
        assert process.stdout.read().strip() == "revoked"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_combined_reconciliation_does_not_take_retry_request_identity(combined_bank):
    from app import evidence

    draft, request = _validated_payment(combined_bank)
    gateway.execute(request, **combined_bank.paths)
    token = evidence.context.set({"session_id": "s_unrelated", "revision": "unrelated"})
    try:
        assert gateway.execute(request, **combined_bank.paths).status == "executed"
    finally:
        evidence.context.reset(token)
    assert not [
        r
        for r in evidence.timeline("s_unrelated", 100, False)["entries"]
        if r["event"] == "bank_transaction_committed"
    ]
    assert (
        len(
            [
                r
                for r in evidence.timeline(draft.session_id, 100, False)["entries"]
                if r["event"] == "bank_transaction_committed"
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "operator,below,equal,above",
    [
        ("lt", "met", "not_met", "not_met"),
        ("lte", "met", "met", "not_met"),
        ("gt", "not_met", "not_met", "met"),
        ("gte", "not_met", "met", "met"),
        ("eq", "not_met", "met", "not_met"),
        ("ne", "met", "not_met", "met"),
    ],
)
def test_quote_condition_decimal_comparisons(operator, below, equal, above):
    from datetime import UTC

    from app.contracts import QuoteCondition

    predicate = QuoteCondition(ticker="MSFT", operator=operator, threshold="300.00", currency="USD")
    for price, expected in [("299.99", below), ("300.00", equal), ("300.01", above)]:
        quote = QuoteResponse(
            ticker="MSFT",
            company_name="Microsoft",
            price=price,
            currency="USD",
            as_of=datetime.now(UTC),
        )
        assert readonly.evaluate_condition(predicate, quote)["state"] == expected
    assert (
        readonly.evaluate_condition(predicate, quote.model_copy(update={"currency": "SGD"}))[
            "state"
        ]
        == "unknown"
    )
    assert (
        readonly.evaluate_condition(
            predicate, quote.model_copy(update={"as_of": datetime.now(UTC) - timedelta(minutes=2)})
        )["state"]
        == above
    )
    assert (
        readonly.evaluate_condition(
            predicate, quote.model_copy(update={"as_of": datetime.now(UTC) + timedelta(minutes=2)})
        )["state"]
        == above
    )


def test_demo_gateway_does_not_recheck_condition_and_retries_never_redebit(
    gateway_environment, monkeypatch
):
    from datetime import UTC

    from app.contracts import QuoteCondition

    env = gateway_environment
    quote = QuoteResponse(
        ticker="MSFT",
        company_name="Microsoft",
        price="250.00",
        currency="USD",
        as_of=datetime.now(UTC),
    )
    monkeypatch.setattr(readonly, "get_quote", lambda _: quote)
    draft = _risk_draft("500.00").model_copy(
        update={
            "condition": QuoteCondition(
                ticker="MSFT", operator="lt", threshold="300.00", currency="USD"
            )
        }
    )
    _, _, payment = _gateway_challenge(env, draft)
    quote = quote.model_copy(update={"price": Decimal("350.00")})
    result = gateway.execute(payment, **env.paths)
    assert result.status == "executed"
    quote = quote.model_copy(update={"price": Decimal("350.00")})
    assert gateway.execute(payment, **env.paths) == result
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


def test_demo_gateway_does_not_read_quote_at_confirmation(gateway_environment, monkeypatch):
    from app.contracts import QuoteCondition

    env = gateway_environment

    def unexpected_read(*args):
        raise AssertionError("Condition must not be checked again")

    monkeypatch.setattr(readonly, "evaluate_condition", unexpected_read)
    draft = _risk_draft("500.00").model_copy(
        update={
            "condition": QuoteCondition(
                ticker="AAPL", operator="lt", threshold="300.00", currency="USD"
            )
        }
    )
    _gateway_challenge(env, draft)
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 0


def test_catalogue_sync_is_additive_audited_and_idempotent(ledger_paths, monkeypatch):
    seed = copy.deepcopy(readonly._load_database())
    original = copy.deepcopy(seed)
    original["users"]["u_demo"]["payees"] = original["users"]["u_demo"]["payees"][:4]
    monkeypatch.setattr(readonly, "_load_database", lambda: original)
    ledger.reconcile_audit(**ledger_paths)
    before = readonly._read_ledger_state(ledger_paths["storage_path"])
    monkeypatch.setattr(readonly, "_load_database", lambda: seed)
    assert ledger.sync_demo_catalogue(**ledger_paths) == len(seed["users"]["u_demo"]["payees"]) - 4
    after = readonly._read_ledger_state(ledger_paths["storage_path"])
    for key in ("accounts", "holdings", "billers"):
        assert before["users"]["u_demo"][key] == after["users"]["u_demo"][key]
    for key in ("transactions", "executions", "payment_control", "next_reference"):
        assert before[key] == after[key]
    assert ledger.sync_demo_catalogue(**ledger_paths) == 0
    events = audit.read_events(100, storage_path=ledger_paths["audit_path"])
    assert sum(e.event == "catalogue_updated" for e in events.entries) == 1


def test_persistent_browser_ownership_survives_cache_loss(combined_bank):
    from app import transaction_state as state

    draft = _risk_draft("50.00")
    token = state.client.set("original-browser")
    try:
        state.start_turn(draft.user_id, draft.session_id)
        state.register(draft, [], {})
        record, _ = state.begin(draft)
        receipt = state.finish(record, {"status": "match"})["receipt"]
        state._records.clear()
        state._turns.clear()
        state._owners.clear()
        state.client.set("another-browser")
        with pytest.raises(state.InvalidDraft):
            state.start_turn(draft.user_id, draft.session_id)
        with pytest.raises(state.InvalidDraft):
            state.authorize(draft, receipt)
        state.cancel(draft)
        state.client.set("original-browser")
        assert state.authorize(draft, receipt).digest == state.digest(draft)
    finally:
        state.client.reset(token)


@pytest.mark.usefixtures("financial_kernel")
def test_live_history_tracks_committed_actions_without_replaying_balances(
    ledger_store, monkeypatch
):
    monkeypatch.setattr(readonly, "_ledger_path", lambda path: path or ledger_store["storage_path"])
    draft = _ledger_draft(
        _ledger_action(), _ledger_action("stock_order", quantity=1), _ledger_action("fx_conversion")
    )
    ledger.execute_batch(draft, "ex_history_view", **ledger_store)
    ledger.execute_batch(draft, "ex_history_view", **ledger_store)
    before = ledger_store["storage_path"].read_bytes()
    history = readonly.list_transactions("u_demo")
    new = [t for t in history if not t.transaction_id.startswith("tx_seed_")]
    assert len(new) == 4
    assert len({t.transaction_id for t in new}) == 4
    fx = [t for t in new if t.transaction_id.startswith("tx_00000003")]
    assert {(t.direction, t.currency, t.amount) for t in fx} == {
        ("debit", "SGD", Decimal("1000.00")),
        ("credit", "USD", Decimal("740.00")),
    }
    insights = readonly.get_account_insights("u_demo")
    assert insights["accounts"][0]["balance"] == "3100.00"
    assert {t["id"] for t in insights["transactions"]} == {t.transaction_id for t in history}
    assert not any(
        t["is_spending"] for t in insights["transactions"] if t["id"].startswith("tx_000")
    )
    assert ledger_store["storage_path"].read_bytes() == before


@pytest.mark.usefixtures("financial_kernel")
def test_financial_context_uses_current_obligations_and_explicit_categories(
    ledger_store, monkeypatch
):
    monkeypatch.setattr(readonly, "_ledger_path", lambda path: path or ledger_store["storage_path"])
    original = readonly.get_account_insights("u_demo")
    rows = original["financial"]["accounts"]
    assert rows[0]["reserve"] == "500.00"
    assert "reserve" not in rows[1]
    for period in original["spending"]:
        for name in ("previous", "current"):
            data = period[name]
            assert sum((Decimal(c["amount"]) for c in data["categories"]), Decimal(0)) == Decimal(
                data["total"]
            )
            assert not {"Transfers", "Investments"} & {c["label"] for c in data["categories"]}
    ledger.execute_batch(
        _ledger_draft(_ledger_action("bill_payment")), "ex_bill_view", **ledger_store
    )
    assert not any("9012" in b.label for b in readonly.list_bills("u_demo"))
    updated = readonly.get_account_insights("u_demo")
    assert updated["accounts"][0]["balance"] == "1960.00"
    assert all(
        not any("DBS" in b["label"] for b in a.get("commitments", {}).get("bills", []))
        for a in updated["financial"]["accounts"]
    )
    new = next(t for t in updated["transactions"] if t["id"] == "tx_00000001")
    assert new["category"] == "Bills" and new["is_spending"] is True


def test_old_runtime_accepts_only_matching_category_annotations(bank_seed, monkeypatch):
    user = copy.deepcopy(bank_seed["users"]["u_demo"])
    user["transaction_history"] = user["transaction_history"][:12]
    user["accounts"][0].pop("reserve")
    for record in user["transaction_history"]:
        record.pop("category")
        record.pop("is_spending")
    monkeypatch.setattr(readonly, "_current_user", lambda *args, **kwargs: user)
    data = readonly.get_account_insights("u_demo")
    assert data["financial"]["accounts"][0]["reserve"] == "500.00"
    assert (
        next(t for t in data["transactions"] if t["id"] == "tx_seed_0003")["category"] == "Housing"
    )
    user["transaction_history"][2]["amount"] = "1.00"
    user["accounts"][0]["reserve"] = "250.00"
    data = readonly.get_account_insights("u_demo")
    assert data["financial"]["accounts"][0]["reserve"] == "250.00"
    assert (
        next(t for t in data["transactions"] if t["id"] == "tx_seed_0003")["category"]
        == "Uncategorised"
    )

    assert data["spending"] is None


def test_statement_projects_internal_moves_and_sales_without_cross_user_leaks(bank_seed):
    user = bank_seed["users"]["u_demo"]
    move = {
        "type": "account_transfer",
        "from_account_id": "acc_1234",
        "from_account_label": "Savings",
        "to_account_id": "acc_5678",
        "to_account_label": "Current",
        "currency": "SGD",
        "amount": "20.00",
    }
    transaction = {
        "user_id": "u_demo",
        "reference": "tx_00000001",
        "completed_at": "2026-10-16T10:00:00+08:00",
        "action": move,
        "execution_details": {},
    }
    other = {**transaction, "user_id": "other", "reference": "tx_00000002"}
    sale = {
        **transaction,
        "reference": "tx_00000003",
        "action": _ledger_action("stock_order", side="sell", quantity=1),
        "execution_details": {"gross_amount": "227.50"},
    }
    history = readonly._committed_history(user, [transaction, other, sale])[19:]
    assert len(history) == 3
    assert [(r["direction"], r["amount"]) for r in history] == [
        ("debit", "20.00"),
        ("credit", "20.00"),
        ("credit", "227.50"),
    ]
    assert all(r["is_spending"] is False for r in history)


def _otp_challenge(env: SimpleNamespace) -> tuple[Any, ExecuteRequest]:
    draft = _risk_draft("500.00")
    context = env.approve(draft)
    challenge = gateway.issue_challenge(
        ChallengeRequest(draft=draft, receipt=_synthetic_receipt(draft, context), require_otp=True),
        **env.paths,
    )
    return challenge, _sign_payment(env.private, challenge)


def test_gateway_require_otp_demands_and_verifies_the_code_before_commit(
    gateway_environment: SimpleNamespace,
) -> None:
    """D-054: an unsure voice makes the client ask for the one-time-code step."""
    env = gateway_environment
    challenge, request = _otp_challenge(env)
    first = gateway.execute(request, **env.paths)
    assert first.status == "needs_extra_check" and first.method == "otp"
    assert _gateway_state(env)["challenges"][challenge.nonce]["status"] == "step_up"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 0
    wrong = gateway.execute(request.model_copy(update={"otp": "000000"}), **env.paths)
    assert wrong.status == "needs_extra_check"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 0
    done = gateway.execute(request.model_copy(update={"otp": config.DEMO_OTP}), **env.paths)
    assert done.status == "executed"
    assert _gateway_state(env)["challenges"][challenge.nonce]["status"] == "committed"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


def test_gateway_require_otp_locks_after_too_many_wrong_codes(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    challenge, request = _otp_challenge(env)
    outcomes = [
        gateway.execute(request.model_copy(update={"otp": "999999"}), **env.paths).status
        for _ in range(env.limits.max_attempts)
    ]
    assert outcomes[-1] == "rejected" and set(outcomes[:-1]) == {"needs_extra_check"}
    assert _gateway_state(env)["challenges"][challenge.nonce]["status"] == "locked"
    with pytest.raises(gateway.GatewayRejected):  # a locked nonce is dead, even with the right code
        gateway.execute(request.model_copy(update={"otp": config.DEMO_OTP}), **env.paths)
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 0


def test_gateway_without_require_otp_ignores_a_supplied_code(
    gateway_environment: SimpleNamespace,
) -> None:
    env = gateway_environment
    _, _, request = _gateway_challenge(env)
    reply = gateway.execute(request.model_copy(update={"otp": "000000"}), **env.paths)
    assert reply.status == "executed"


@pytest.mark.parametrize("require_otp", [False, True])
def test_voice_fingerprint_step_up_preserves_signature_otp_and_exactly_once(
    gateway_environment: SimpleNamespace,
    require_otp: bool,
) -> None:
    env = gateway_environment
    draft = _risk_draft("500.00")
    context = env.approve(draft)
    challenge = gateway.issue_challenge(
        ChallengeRequest(
            draft=draft,
            receipt=_synthetic_receipt(draft, context),
            require_fingerprint=True,
            require_otp=require_otp,
        ),
        **env.paths,
    )
    request = _sign_payment(env.private, challenge)
    first = gateway.execute(request, **env.paths)
    assert first.status == "needs_extra_check" and first.method == "fingerprint"
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 0
    confirmed = request.model_copy(update={"fingerprint": True})
    if require_otp:
        extra = gateway.execute(confirmed, **env.paths)
        assert extra.status == "needs_extra_check" and extra.method == "otp"
        assert len(_ledger_state(env.ledger_paths)["transactions"]) == 0
        confirmed = confirmed.model_copy(update={"otp": config.DEMO_OTP})
    done = gateway.execute(confirmed, **env.paths)
    assert done.status == "executed"
    assert gateway.execute(confirmed, **env.paths) == done
    assert len(_ledger_state(env.ledger_paths)["transactions"]) == 1


@pytest.mark.parametrize(
    "count,status,method",
    [
        (2, "allow", "otp"),
        (3, "needs_extra_check", "fingerprint"),
        (9, "needs_extra_check", "fingerprint"),
        (10, "reject", "otp"),
    ],
)
def test_demo_velocity_fingerprint_threshold(
    count: int,
    status: str,
    method: str,
    risk_policy: rules.TransferPolicy,
    risk_state: dict[str, Any],
    risk_now: datetime,
) -> None:
    policy = risk_policy.model_copy(
        update={
            "velocity_step_up_at": 3,
            "velocity_max_transfers": 10,
            "velocity_window_minutes": 1,
        }
    )
    for index in range(count):
        _risk_history(
            risk_state, amount="1.00", completed_at=risk_now - timedelta(seconds=index + 1)
        )
    decision = _risk_evaluate(policy, risk_state, risk_now)
    assert (decision.status, decision.method) == (status, method)
