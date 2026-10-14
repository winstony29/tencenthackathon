"""Mock bank tests: gateway, rules, ledger and devices.

Owner: Jayanth
Diagram box: Mock gateway, Risk & KYC rules, Mock ledger
Contract: docs/CONTRACTS.md §3.3 and §4
"""

import ast
import base64
import hashlib
import importlib
import json
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import audit
from app.bank import devices, ledger, readonly
from app.contracts import (
    Account,
    AccountsResponse,
    Draft,
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
    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
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
    assert audit_path.read_text() == content
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
    assert len(calls) == 2

    def fail(descriptor: int) -> None:
        raise OSError("sensitive-test-value")

    monkeypatch.setattr(audit.os, "fsync", fail)
    with pytest.raises(audit.AuditError, match="Audit storage operation failed"):
        audit.append_event("gateway", "nonce_replay_detected", {})


def test_audit_hash_can_be_independently_recomputed(audit_path: Path) -> None:
    audit.append_event("validation", "validation_mismatch", {"amount": "50.00"})
    record = json.loads(audit_path.read_text())
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
    assert json.loads(device_path.read_text()) == {"u_demo": expected}
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
    assert device_path.read_text() == content
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
    assert device_path.read_text() == "{}\n"
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
    assert json.loads(device_path.read_text())["u_demo"]["x"] == public_jwk["x"]
    assert len(calls) == 2
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
    tree = ast.parse(Path(readonly.__file__).read_text())
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


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"])
def test_ledger_buys_all_five_seed_stocks(
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


def test_ledger_unknown_user_cannot_use_another_users_accounts(
    ledger_store: dict[str, Path],
) -> None:
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="unknown_user"):
        ledger.execute_batch(_ledger_draft(user_id="u_other"), "ex_foreign", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


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


def test_ledger_ordered_batch_can_fund_stock_purchase_with_explicit_fx(
    ledger_store: dict[str, Path],
) -> None:
    fx = _ledger_action("fx_conversion", sell_amount="10000.00", buy_amount="7400.00")
    stock = _ledger_action("stock_order", quantity=160)
    response = ledger.execute_batch(_ledger_draft(fx, stock), "ex_funded_buy", **ledger_store)
    assert [result.action_index for result in response.results] == [0, 1]
    assert _ledger_balance(ledger_store, "acc_5678") == "55000.00"
    assert _ledger_balance(ledger_store, "acc_7788") == "1000.00"


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
def test_ledger_changed_payload_cannot_reuse_committed_identifier(
    changes: dict[str, Any], ledger_store: dict[str, Path]
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_reused", **ledger_store)
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="execution_conflict"):
        ledger.execute_batch(_ledger_draft(_ledger_action(**changes)), "ex_reused", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


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


def test_ledger_process_race_prevents_double_spending(ledger_store: dict[str, Path]) -> None:
    script = """
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


def test_ledger_real_process_exit_after_replacement_recovers_once(
    ledger_store: dict[str, Path],
) -> None:
    script = """
import json, os, sys
from pathlib import Path
from app.bank import ledger
from app.contracts import Draft
ledger._sync_directory = lambda path: os._exit(73)
ledger.execute_batch(Draft.model_validate(json.loads(sys.argv[1])), "ex_crash",
    storage_path=Path(sys.argv[2]), audit_path=Path(sys.argv[3]))
"""
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
def test_ledger_requires_safe_gateway_execution_identifier(
    identifier: Any, ledger_paths: dict[str, Path]
) -> None:
    with pytest.raises(ledger.LedgerRejected, match="invalid_execution_id"):
        ledger.execute_batch(_ledger_draft(), identifier, **ledger_paths)
    assert not ledger_paths["storage_path"].exists()


def test_ledger_revalidates_mutated_draft(ledger_store: dict[str, Path]) -> None:
    draft = _ledger_draft()
    draft.actions[0].amount = 1.5
    before = ledger_store["storage_path"].read_bytes()
    with pytest.raises(ledger.LedgerRejected, match="invalid_draft"):
        ledger.execute_batch(draft, "ex_float", **ledger_store)
    assert ledger_store["storage_path"].read_bytes() == before


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
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.bank":
                if any(alias.name == "ledger" for alias in node.names):
                    assert path.name == "gateway.py"
            if isinstance(node, ast.ImportFrom) and node.module == "app.bank.ledger":
                assert path.name == "gateway.py"
    # The router delegates to the gateway; it never imports the ledger directly.
    from app.bank import router

    assert "ledger" not in vars(router)


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


def test_ledger_missing_state_and_marker_cannot_reset_when_execution_audits_exist(
    ledger_store: dict[str, Path],
) -> None:
    ledger.execute_batch(_ledger_draft(), "ex_before_loss", **ledger_store)
    ledger_store["storage_path"].unlink()
    ledger_store["storage_path"].with_suffix(".lock").unlink()
    with pytest.raises(readonly.LedgerStorageError, match="ledger state is missing"):
        ledger.execute_batch(_ledger_draft(), "ex_after_loss", **ledger_store)
    assert not ledger_store["storage_path"].exists()


def test_ledger_invalid_audit_prevents_first_initialisation(ledger_paths: dict[str, Path]) -> None:
    ledger_paths["audit_path"].write_text("incomplete")
    with pytest.raises(ledger.LedgerAuditPending):
        ledger.execute_batch(_ledger_draft(), "ex_unsafe_init", **ledger_paths)
    assert not ledger_paths["storage_path"].exists()


@pytest.mark.parametrize("kind", ["transfer", "bill_payment", "stock_order"])
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
    assert set(integrated_views["storage_path"].parent.iterdir()) == before
    ledger.reconcile_audit(**integrated_views)
    assert (
        TestClient(app).get("/api/accounts?user_id=u_demo").json()["accounts"][0]["balance"]
        == "3200.00"
    )


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
    assert len(calls) == 2
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


def test_integration_real_crash_after_audit_append_reuses_exact_evidence(
    integrated_views: dict[str, Path],
) -> None:
    ledger.reconcile_audit(**integrated_views)
    script = """
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
