"""Validation endpoint: compares a draft with its own transcript.

Owner: Jayanth
Diagram box: Validation agent
Contract: docs/CONTRACTS.md §3.2
"""

from typing import Any

from fastapi import APIRouter

from app.contracts import ValidateRequest, ValidateResponse

router = APIRouter(prefix="/api", tags=["validation"])

# Stub response copied from CONTRACTS §3.2 (CONTRACTS §7); J-05 replaces it.
_STUB_MATCH = {"status": "match", "receipt": "vr_3f9a…"}


@router.post("/validate", response_model=ValidateResponse)
async def validate(request: ValidateRequest) -> dict[str, Any]:
    """Check every field of the draft against the transcript. Stub: always a match."""
    return _STUB_MATCH
