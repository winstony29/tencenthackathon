"""Validation endpoint: unavailable until trusted instruction validation is connected.

Owner: Jayanth
Diagram box: Validation agent
Contract: docs/CONTRACTS.md §3.2
"""

from fastapi import APIRouter, HTTPException

from app.contracts import ValidateRequest, ValidateResponse

router = APIRouter(prefix="/api", tags=["validation"])


@router.post("/validate", response_model=ValidateResponse)
async def validate(request: ValidateRequest) -> ValidateResponse:
    """Fail closed; a client transcript is not an authoritative instruction record."""
    raise HTTPException(503, "Independent validation unavailable; trusted integration required.")
