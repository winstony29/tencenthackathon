"""Semantic validation and explicit revocation routes."""

from fastapi import APIRouter

from app import transaction_state
from app.contracts import ValidateRequest, ValidateResponse
from app.validation.validator import validate as audit

router = APIRouter(prefix="/api", tags=["validation"])


@router.post("/validate", response_model=ValidateResponse)
async def validate(request: ValidateRequest):
    return await audit(request.draft)


@router.post("/validation/cancel")
async def cancel(request: ValidateRequest):
    transaction_state.cancel(request.draft)
    return {"status": "cancelled"}
