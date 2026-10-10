"""Bank endpoints: accounts, quotes, device keys, nonces, execution and the audit log.

Owner: Jayanth
Diagram box: Mock gateway, User data, Audit log
Contract: docs/CONTRACTS.md §3.3
"""

from typing import Any

from fastapi import APIRouter

from app.config import AUDIT_DEFAULT_LIMIT
from app.contracts import (
    AccountsResponse,
    AuditResponse,
    ChallengeRequest,
    ChallengeResponse,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    ExecuteRequest,
    ExecuteResponse,
    QuoteResponse,
)

router = APIRouter(prefix="/api", tags=["bank"])

# Stub responses copied from CONTRACTS §3.3 (CONTRACTS §7); J-02 to J-08 replace them.
_STUB_ACCOUNTS = {
    "accounts": [
        {
            "account_id": "acc_1234",
            "label": "Savings …1234",
            "type": "savings",
            "currency": "SGD",
            "balance": "3200.00",
        }
    ],
    "payees": [{"payee_id": "pay_4521", "name": "John Doe", "label": "John Doe …4521"}],
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
_STUB_QUOTE = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "price": "227.50",
    "currency": "USD",
    "as_of": "2026-10-12T09:30:00+08:00",
}
_STUB_REGISTERED = {"status": "registered"}
_STUB_CHALLENGE = {
    "nonce": "n_7d21…",
    "signing_payload": "<canonical draft JSON>.n_7d21…",
    "expires_at": "2026-10-12T09:35:00+08:00",
}
_STUB_EXECUTED = {"status": "executed", "results": [{"action_index": 0, "reference": "TX-000123"}]}
# The CONTRACTS example writes the time as "…"; a real time is used so the stub passes validation.
_STUB_AUDIT = {
    "chain_valid": True,
    "entries": [
        {
            "index": 41,
            "time": "2026-10-12T09:30:00+08:00",
            "actor": "intent",
            "event": "jev_answer",
            "data": {},
            "prev_hash": "…",
            "hash": "…",
        }
    ],
}


@router.get("/accounts", response_model=AccountsResponse)
async def accounts(user_id: str) -> dict[str, Any]:
    """List the user's accounts, payees, billers and holdings. Stub: the CONTRACTS example."""
    return _STUB_ACCOUNTS


@router.get("/quote", response_model=QuoteResponse)
async def quote(ticker: str) -> dict[str, Any]:
    """Return the mock price for a ticker. Stub: always Apple."""
    return _STUB_QUOTE


@router.post("/device/register", response_model=DeviceRegisterResponse)
async def register_device(request: DeviceRegisterRequest) -> dict[str, Any]:
    """Store the phone's public key. Stub: stores nothing."""
    return _STUB_REGISTERED


@router.post("/challenge", response_model=ChallengeResponse)
async def challenge(request: ChallengeRequest) -> dict[str, Any]:
    """Issue a one-time nonce for a validated draft. Stub: does not check the receipt."""
    return _STUB_CHALLENGE


@router.post("/execute", response_model=ExecuteResponse)
async def execute(request: ExecuteRequest) -> dict[str, Any]:
    """Move money for a signed draft. Stub: always executed, and nothing moves."""
    return _STUB_EXECUTED


@router.get("/audit", response_model=AuditResponse)
async def audit(limit: int = AUDIT_DEFAULT_LIMIT) -> dict[str, Any]:
    """Return the latest audit entries and whether the chain is intact. Stub: one entry."""
    return _STUB_AUDIT
