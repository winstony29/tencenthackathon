"""Bank endpoints: accounts, quotes, device keys, nonces, execution and the audit log.

Owner: Jayanth
Diagram box: Mock gateway, User data, Audit log
Contract: docs/CONTRACTS.md §3.3
"""

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.audit import read_events
from app.bank import gateway
from app.bank.devices import register_public_key
from app.bank.readonly import get_accounts, get_fx_rate, get_quote
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
    FxResponse,
    QuoteResponse,
    RejectedResponse,
)

router = APIRouter(prefix="/api", tags=["bank"])


@router.get("/accounts", response_model=AccountsResponse)
async def accounts(user_id: str) -> AccountsResponse:
    """List the user's public accounts, payees, billers and holdings from committed state."""
    return get_accounts(user_id)


@router.get("/quote", response_model=QuoteResponse)
async def quote(ticker: str) -> QuoteResponse:
    """Return the stored mock quote for the requested ticker."""
    return get_quote(ticker)


@router.get("/fx", response_model=FxResponse)
async def fx(from_currency: str = Query(alias="from"), to: str = Query()) -> FxResponse:
    """Return the stored mock rate for one currency pair; an unknown pair is 404."""
    try:
        return get_fx_rate(from_currency, to)
    except LookupError:
        raise HTTPException(status_code=404, detail="Rate not found.") from None


@router.post("/device/register", response_model=DeviceRegisterResponse)
async def register_device(request: DeviceRegisterRequest) -> DeviceRegisterResponse:
    """Pin the public key for controlled demo enrollment; customer identity is not verified."""
    return register_public_key(request.user_id, request.public_key_jwk)


@router.post("/challenge", response_model=ChallengeResponse)
async def challenge(request: ChallengeRequest) -> ChallengeResponse:
    """Issue a nonce only after receipt and current trusted-context checks pass."""
    try:
        return await run_in_threadpool(gateway.issue_challenge, request)
    except gateway.GatewayRejected:
        raise HTTPException(400, "Invalid or unavailable transaction approval.") from None
    except gateway.GatewayUnavailable:
        raise HTTPException(
            503, "Transaction approval unavailable; operator review required."
        ) from None


@router.post("/execute", response_model=ExecuteResponse)
async def execute(request: ExecuteRequest) -> ExecuteResponse:
    """Execute a verified approval or recover its actual previously committed result."""
    try:
        return await run_in_threadpool(gateway.execute, request)
    except gateway.GatewayRejected as error:
        if str(error) in {"bad_signature", "unknown_nonce", "nonce_used", "nonce_expired"}:
            return RejectedResponse(
                status="rejected",
                reason=str(error),
                message="This transaction approval was rejected.",
            )
        raise HTTPException(400, "Invalid or unavailable transaction approval.") from None
    except gateway.GatewayUnavailable:
        raise HTTPException(
            503, "Transaction outcome unavailable; operator review required."
        ) from None


@router.get("/audit", response_model=AuditResponse)
async def audit(limit: int = Query(default=AUDIT_DEFAULT_LIMIT, gt=0)) -> AuditResponse:
    """Return real audit entries and verify the entire stored chain."""
    return read_events(limit)
