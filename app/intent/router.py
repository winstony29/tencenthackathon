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
    "objective": "prepare",
    "scenario": None,
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
    masked = options.mask(request.text)
    key = " ".join(masked.lower().split())
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
    reply = await jev.ask(masked, [_PREVIEW_QUESTION])
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

    A transcript with a known attack phrase is blocked before any model sees it. In a
    session that already holds a draft it is a follow-up turn about that draft;
    otherwise it starts a new command. Either way one Jev batch on the original text
    (numbers masked) also carries the input shield's question; if Jev flags an injection,
    nothing else from that batch is used. An answer settles the open question from the
    stored replies without asking Jev again.
    """
    if not _live():
        return _stub_understand(request)
    started = time.monotonic()
    if request.transcript is not None:
        if shield.obvious(request.transcript):
            session.drop(request.session_id)
            return _blocked("", started)
        current = session.load(request.session_id, request.user_id)
        if current and current.draft:
            await _start_followup(current, request.transcript)
        else:
            current = await _start(request.user_id, request.transcript)
        if shield.flagged(current.replies[0]):
            session.drop(request.session_id)
            return _blocked(current.replies[0].model, started)
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
    if current.followup is not None:
        return await _followup_step(request, current, started)
    return _command_step(request, current, _meta(current, started))


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
    # Jev reads the text with numbers masked (D-026). W-04: ask the K rewrites of that masked
    # text only after this batch clears the shield (it carries the shield question), so
    # injected text never reaches the rewriter's LLM; rewrites must keep the [Nk] markers.
    phrasings = [options.mask(transcript)]
    replies = await jev.ask_all(phrasings, built.questions)
    return session.Session(user_id, transcript, built, data, replies)


async def _start_followup(current: session.Session, text: str) -> None:
    current.options = options.build_followup(text, current.draft, current.data)
    context = {"current_tasks": [options.describe(a) for a in current.draft.actions]}
    current.replies = await jev.ask_all([options.mask(text)], current.options.questions, context)
    current.followup, current.answers, current.asked, current.pending = text, {}, 0, None


def _amount(current: session.Session) -> Decimal:
    # ponytail: the largest number said stands in for the amount when choosing tau; the exact
    # amount needs the picks this decision is still making.
    return max(current.options.numbers.values(), default=Decimal(0))


def _command_step(
    request: UnderstandRequest, current: session.Session, meta: dict
) -> dict[str, Any]:
    decision = gate.decide(current.replies, current.answers, _amount(current))
    if decision.ask:
        question = _ask(request, current, decision, meta)
        if question:
            return question
        decision = gate.Decision(decision.picks, decision.actions, missing=decision.ask)
    if decision.missing or not decision.actions:
        session.drop(request.session_id)
        return _unsupported(_MISSING.get(decision.missing, _HELP), meta)
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
        session.drop(request.session_id)
        return _unsupported(str(error), meta)
    # The objective only shapes the screen on a first command, so an unsure one means "prepare".
    p = gate.pick(current.replies, "objective")
    objective = p.option if gate.trusted(p, _amount(current)) else "prepare"
    return _keep_draft(request, current, built, objective, None, meta)


async def _followup_step(
    request: UnderstandRequest, current: session.Session, started: float
) -> dict[str, Any]:
    meta = _meta(current, started)
    first = gate.decide_each(current.replies, current.answers, _amount(current), ["objective"])
    if first.ask:
        return _ask(request, current, first, meta) or _unsupported(_HELP, meta)
    objective = first.picks["objective"].option
    if objective == "new_task":
        fresh = await _start(current.user_id, current.followup)
        return _command_step(request, fresh, _meta(fresh, started))
    skip = {"objective", shield.QUESTION.name}
    names = [q.name for q in current.options.questions if q.name not in skip]
    rest = gate.decide_each(current.replies, current.answers, _amount(current), names)
    if rest.ask:
        return _ask(request, current, rest, meta) or _unsupported(_HELP, meta)
    rest.picks["objective"] = first.picks["objective"]
    try:
        updated, scenario = draft.amend(
            current.draft, rest, objective, current.followup, current.options, current.data
        )
    except draft.Unbuildable as error:
        return _unsupported(str(error), meta)
    return _keep_draft(request, current, updated, objective, scenario, meta)


def _ask(
    request: UnderstandRequest, current: session.Session, decision: gate.Decision, meta: dict
) -> dict | None:
    """Return a clarifying question about decision.ask, or None when no chips can be offered."""
    p = decision.picks[decision.ask]
    text, chips = options.clarify(current.options, decision.ask, p.probabilities)
    if not chips:
        return None
    if current.asked >= config.MAX_QUESTIONS:
        session.drop(request.session_id)
        return _cancelled("too_many_questions", meta)
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


def _keep_draft(request, current, built, objective, scenario, meta) -> dict[str, Any]:
    # The session stays open after a draft so follow-up turns can amend it (CONTRACTS §3.1).
    current.draft, current.followup, current.pending = built, None, None
    session.save(request.session_id, current)
    return {
        "status": "draft",
        "draft": built,
        "objective": objective,
        "scenario": scenario,
        "meta": meta,
    }


def _blocked(model: str, started: float) -> dict[str, Any]:
    # No model means the phrase backstop blocked it before any Jev call.
    return {
        "status": "blocked",
        "reason": "possible_injection",
        "message": _BLOCKED,
        "meta": {
            "latency_ms": round((time.monotonic() - started) * 1000),
            "model": model,
            "source": "jev" if model else "none",
            "phrasings": 1 if model else 0,
            "cached": False,
        },
    }


_HELP = "I can help with transfers, bill payments and stock orders."
_BLOCKED = "This request contains instructions I can't act on. Please say it again."
_MISSING = {
    "transfer_amount": 'Please say how much to send, for example "send fifty to John".',
    "quantity": 'Please say how many shares, for example "buy 100 Apple shares".',
    "limit_price": "Please say the price for your order.",
    "several_transfers": "I can do one transfer at a time. Please say them one by one.",
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
