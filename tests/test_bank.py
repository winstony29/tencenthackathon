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
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import audit
from app.bank import devices, readonly
from app.contracts import Account, AccountsResponse, QuoteResponse
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
def device_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
) -> None:
    source = (
        "import sys,json; from pathlib import Path; from fastapi import HTTPException; "
        "from app.bank.devices import register_public_key\n"
        "try:\n register_public_key('u_demo', json.loads(sys.argv[2]), "
        "storage_path=Path(sys.argv[1]))\n"
        "except HTTPException as error:\n sys.exit(2 if error.status_code == 400 else 3)\n"
    )
    keys = [public_jwk, _other_public_jwk(public_jwk)] * 2
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", source, str(device_path), json.dumps(key)],
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
def test_cashflow_unknown_users_or_accounts_return_not_found(user_id: str, account_id: str) -> None:
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
