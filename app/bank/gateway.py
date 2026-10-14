"""Receipt-gated challenges. Ledger execution remains unavailable, never fake success."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from app import transaction_state as state
from app.contracts import Draft

# Single-process demo only; no ledger calls until signing/risk/atomic execution is implemented.
_challenges: dict[str, tuple[Draft, str, datetime]] = {}


def challenge(draft: Draft, receipt: str) -> dict:
    try:
        state.authorize(draft, receipt)
    except state.InvalidDraft as error:
        raise HTTPException(409, str(error)) from error
    now = datetime.now(UTC)
    for nonce in [n for n, (_, _, expires) in _challenges.items() if expires <= now]:
        del _challenges[nonce]
    if len(_challenges) >= 1000:
        raise HTTPException(503, "Too many pending confirmations.")
    nonce = "n_" + secrets.token_hex(24)
    expires = now + timedelta(minutes=2)
    _challenges[nonce] = (draft.model_copy(deep=True), receipt, expires)
    return {
        "nonce": nonce,
        "signing_payload": state.canonical(draft) + "." + nonce,
        "expires_at": expires.isoformat(),
    }


def execute(nonce: str) -> dict:
    pending = _challenges.get(nonce)
    if pending is None:
        raise HTTPException(409, "Unknown confirmation.")
    draft, receipt, expires = pending
    if expires <= datetime.now(UTC):
        raise HTTPException(409, "Confirmation expired.")
    try:
        state.authorize(draft, receipt)
    except state.InvalidDraft as error:
        raise HTTPException(409, str(error)) from error
    raise HTTPException(503, "Bank execution is not connected. No money was moved.")
