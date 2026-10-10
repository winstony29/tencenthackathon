"""Intent endpoints: speech to text, and text to a question or a draft.

Owner: Winston
Diagram box: Speech to text, Confidence gate, Draft builder (Intent detection column)
Contract: docs/CONTRACTS.md §3.1
"""

from typing import Any

from fastapi import APIRouter, UploadFile

from app.contracts import (
    NONE_OPTION_ID,
    PreviewRequest,
    PreviewResponse,
    TranscribeResponse,
    UnderstandRequest,
    UnderstandResponse,
)

router = APIRouter(prefix="/api", tags=["intent"])

# Stub responses copied from CONTRACTS §3.1 (CONTRACTS §7); W-06 to W-11 replace them.
_STUB_META = {
    "latency_ms": 1840,
    "model": "jev-1.13.0",
    "source": "jev",
    "phrasings": 4,
    "cached": False,
}
_STUB_PREVIEW = {
    "intent": {
        "value": "transfer",
        "confidence": 0.81,
        "probabilities": {
            "transfer": 0.81,
            "bill_payment": 0.11,
            "stock_order": 0.05,
            "none": 0.03,
        },
    },
    "meta": {**_STUB_META, "latency_ms": 210, "phrasings": 1},
}
_STUB_TRANSCRIPT = {"transcript": "send fifty to john", "confidence": 0.93}
_STUB_QUESTION = {
    "status": "question",
    "question": {
        "question_id": "q_1",
        "text": "Which John did you mean?",
        "options": [
            {"option_id": "pay_4521", "label": "John Doe …4521"},
            {"option_id": "pay_8892", "label": "John Smith …8892"},
        ],
        "allow_none": True,
    },
    "evidence": [
        {
            "question": "recipient",
            "pick": "pay_4521",
            "avg_p": 0.52,
            "spread": 0.31,
            "probabilities": {"pay_4521": 0.52, "pay_8892": 0.48},
        }
    ],
    "meta": _STUB_META,
}
_STUB_DRAFT = {
    "status": "draft",
    "draft": {
        "draft_id": "d_8f2c1a",
        "user_id": "u_demo",
        "session_id": "s_41b9",
        "transcript": "send fifty to john",
        "created_at": "2026-10-12T09:30:00+08:00",
        "actions": [
            {
                "type": "transfer",
                "from_account_id": "acc_1234",
                "from_account_label": "Savings …1234",
                "to_payee_id": "pay_4521",
                "to_payee_label": "John Doe …4521",
                "amount": "50.00",
                "currency": "SGD",
            }
        ],
        "evidence": [
            {
                "question": "recipient",
                "pick": "pay_4521",
                "avg_p": 0.97,
                "spread": 0.03,
                "probabilities": {"pay_4521": 0.97, "pay_8892": 0.03},
            }
        ],
    },
    "meta": _STUB_META,
}
_STUB_CANCELLED = {
    "status": "cancelled",
    "reason": "user_declined",
    "message": "Okay, nothing was sent.",
    "meta": {**_STUB_META, "latency_ms": 0, "model": "", "source": "none", "phrasings": 0},
}


@router.post("/preview", response_model=PreviewResponse)
async def preview(request: PreviewRequest) -> dict[str, Any]:
    """Guess the action type while the user types; display only. Stub: always a transfer."""
    return _STUB_PREVIEW


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(audio: UploadFile) -> dict[str, Any]:
    """Turn recorded speech into text. Stub: always returns the CONTRACTS example."""
    return _STUB_TRANSCRIPT


@router.post("/understand", response_model=UnderstandResponse)
async def understand(request: UnderstandRequest) -> dict[str, Any]:
    """Turn a transcript, or the answer to a question, into the next step.

    Stub: a transcript gets the "Which John?" question, "none of these" cancels,
    and any other answer gets the example draft.
    """
    if request.transcript is not None:
        return _STUB_QUESTION
    if request.answer.option_id == NONE_OPTION_ID:
        return _STUB_CANCELLED
    return _STUB_DRAFT
