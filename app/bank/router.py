"""Bank endpoints: accounts, quotes, device keys, nonces, execution and the audit log.

Owner: Jayanth
Diagram box: Mock gateway, User data, Audit log
Contract: docs/CONTRACTS.md §3.3
"""

from typing import Any

from fastapi import APIRouter, Query

from app.audit import read_events
from app.bank.readonly import get_accounts, get_quote
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
_STUB_REGISTERED = {"status": "registered"}
_STUB_CHALLENGE = {
    "nonce": "n_7d21…",
    "signing_payload": "<canonical draft JSON>.n_7d21…",
    "expires_at": "2026-10-12T09:35:00+08:00",
}
_STUB_EXECUTED = {"status": "executed", "results": [{"action_index": 0, "reference": "TX-000123"}]}


@router.get("/accounts", response_model=AccountsResponse)
async def accounts(user_id: str) -> AccountsResponse:
    """List the user's public accounts, payees, billers and holdings from the seed."""
    return get_accounts(user_id)


@router.get("/quote", response_model=QuoteResponse)
async def quote(ticker: str) -> QuoteResponse:
    """Return the stored mock quote for the requested ticker."""
    return get_quote(ticker)


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
async def audit(limit: int = Query(default=AUDIT_DEFAULT_LIMIT, gt=0)) -> AuditResponse:
    """Return real audit entries and verify the entire stored chain."""
    return read_events(limit)
