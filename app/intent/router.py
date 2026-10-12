"""Intent endpoints: speech to text, and text to a question or a draft.

Owner: Winston
Diagram box: Speech to text, Confidence gate, Draft builder (Intent detection column)
Contract: docs/CONTRACTS.md §3.1
"""

import time
from collections import OrderedDict
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile

from app import config
from app.bank import readonly
from app.contracts import (
    NONE_OPTION_ID,
    PreviewRequest,
    PreviewResponse,
    TranscribeResponse,
    UnderstandRequest,
    UnderstandResponse,
)
from app.intent import draft, gate, jev, options, session, shield

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


_PREVIEW_QUESTION = jev.Question(
    "intent",
    "Which banking task does the text ask for so far?",
    {
        "transfer": "Send money to a person.",
        "bill_payment": "Pay a bill, such as a credit card bill.",
        "stock_order": "Buy or sell shares.",
        "none": "None of these yet, or too little text to tell.",
    },
)
_preview_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()


@router.post("/preview", response_model=PreviewResponse)
async def preview(request: PreviewRequest) -> dict[str, Any]:
    """Guess the action type while the user types; display only, never a draft or a value.

    One Jev call on the raw text, cached by normalised text so retyping costs nothing.
    """
    if not config.MAKERS_MODELS_KEY:
        return _STUB_PREVIEW
    key = " ".join(request.text.lower().split())
    if len(key) < 2:
        certain = {o: float(o == "none") for o in _PREVIEW_QUESTION.criteria}
        return {
            "intent": {"value": "none", "confidence": 1.0, "probabilities": certain},
            "meta": {
                "latency_ms": 0,
                "model": "",
                "source": "none",
                "phrasings": 0,
                "cached": False,
            },
        }
    if key in _preview_cache:
        _preview_cache.move_to_end(key)
        hit = _preview_cache[key]
        return {**hit, "meta": {**hit["meta"], "latency_ms": 0, "cached": True}}
    started = time.monotonic()
    reply = await jev.ask(request.text, [_PREVIEW_QUESTION])
    answer = reply.answers["intent"]
    result = {
        "intent": {
            "value": answer.choice,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
        },
        "meta": {
            "latency_ms": round((time.monotonic() - started) * 1000),
            "model": reply.model,
            "source": "jev",
            "phrasings": 1,
            "cached": False,
        },
    }
    _preview_cache[key] = result
    if len(_preview_cache) > config.PREVIEW_CACHE_SIZE:
        _preview_cache.popitem(last=False)
    return result


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(audio: UploadFile) -> dict[str, Any]:
    """Turn recorded speech into text. Stub: always returns the CONTRACTS example."""
    return _STUB_TRANSCRIPT


@router.post("/understand", response_model=UnderstandResponse)
async def understand(request: UnderstandRequest) -> dict[str, Any]:
    """Turn a transcript, or the answer to a question, into the next step.

    A transcript is checked by the input shield first, then starts a session: one
    batch of Jev questions per phrasing. An answer
    settles the open question from the stored replies without asking Jev again.
    The gate then asks the next unsure question, or the draft is built.
    """
    if not _live():
        return _stub_understand(request)
    started = time.monotonic()
    if request.transcript is not None:
        blocked, model = await shield.check(request.transcript)
        if blocked:
            return {
                "status": "blocked",
                "reason": "possible_injection",
                "message": _BLOCKED,
                "meta": {
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "model": model,
                    "source": "jev",
                    "phrasings": 1,
                    "cached": False,
                },
            }
        current = await _start(request.user_id, request.transcript)
    else:
        current = session.load(request.session_id, request.user_id)
        if current is None:
            raise HTTPException(404, "This conversation has expired. Please start again.")
        if request.answer.option_id == NONE_OPTION_ID:
            session.drop(request.session_id)
            return _cancelled("user_declined", _meta(current, started))
        try:
            current.record(request.answer)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
    return _next_step(request, current, _meta(current, started))


def _live() -> bool:
    # ponytail: stubs answer until the Jev key and Jayanth's readonly views exist (CONTRACTS §7);
    # delete this check and the stubs once both are in place everywhere.
    return bool(config.MAKERS_MODELS_KEY) and hasattr(readonly, "get_accounts")


async def _start(user_id: str, transcript: str) -> session.Session:
    try:
        data = options.UserData(readonly.get_accounts(user_id), readonly.list_quotes())
    except LookupError as error:
        raise HTTPException(404, "Unknown user.") from error
    built = options.build(transcript, data)
    phrasings = [transcript]  # W-04 adds the K rewrites; the original always stays first
    replies = await jev.ask_all(phrasings, built.questions)
    return session.Session(user_id, transcript, built, data, replies)


def _next_step(request: UnderstandRequest, current: session.Session, meta: dict) -> dict[str, Any]:
    # ponytail: the largest number said stands in for the amount when choosing tau; the exact
    # amount needs the picks this decision is still making.
    amount = max(current.options.numbers.values(), default=Decimal(0))
    decision = gate.decide(current.replies, current.answers, amount)
    if decision.ask:
        p = decision.picks[decision.ask]
        text, chips = options.clarify(current.options, decision.ask, p.probabilities)
        if chips and current.asked >= config.MAX_QUESTIONS:
            session.drop(request.session_id)
            return _cancelled("too_many_questions", meta)
        if chips:
            current.asked += 1
            question_id = f"q_{current.asked}"
            current.pending = (question_id, decision.ask, {o for o, _ in chips})
            session.save(request.session_id, current)
            return {
                "status": "question",
                "question": {
                    "question_id": question_id,
                    "text": text,
                    "options": [{"option_id": o, "label": label} for o, label in chips],
                    "allow_none": True,
                },
                "evidence": [draft.evidence(p)],
                "meta": meta,
            }
        decision = gate.Decision(decision.picks, decision.actions, missing=decision.ask)
    session.drop(request.session_id)
    if decision.missing:
        return _unsupported(_MISSING.get(decision.missing, _HELP), meta)
    if not decision.actions:
        return _unsupported(_HELP, meta)
    try:
        built = draft.build(
            current.transcript,
            current.user_id,
            request.session_id,
            decision,
            current.options,
            current.data,
        )
    except draft.Unbuildable as error:
        return _unsupported(str(error), meta)
    return {"status": "draft", "draft": built, "meta": meta}


_HELP = "I can help with transfers, bill payments and stock orders."
_BLOCKED = "This request contains instructions I can't act on. Please say it again."
_MISSING = {
    "transfer_amount": 'Please say how much to send, for example "send fifty to John".',
    "quantity": 'Please say how many shares, for example "buy 100 Apple shares".',
    "limit_price": "Please say the price for your order.",
}


def _meta(current: session.Session, started: float) -> dict[str, Any]:
    return {
        "latency_ms": round((time.monotonic() - started) * 1000),
        "model": current.replies[0].model,
        "source": "jev",
        "phrasings": len(current.replies),
        "cached": bool(current.answers) or current.pending is not None,
    }


def _cancelled(reason: str, meta: dict) -> dict[str, Any]:
    messages = {
        "user_declined": "Okay, nothing was sent.",
        "too_many_questions": "I couldn't work out what you meant. Please try again.",
    }
    return {"status": "cancelled", "reason": reason, "message": messages[reason], "meta": meta}


def _unsupported(message: str, meta: dict) -> dict[str, Any]:
    return {"status": "unsupported", "message": message, "meta": meta}


def _stub_understand(request: UnderstandRequest) -> dict[str, Any]:
    if request.transcript is not None:
        return _STUB_QUESTION
    if request.answer.option_id == NONE_OPTION_ID:
        return _STUB_CANCELLED
    return _STUB_DRAFT
