"""Intent endpoints: speech to text, and text to a question or a draft.

Owner: Winston
Diagram box: Speech to text, Confidence gate, Draft builder (Intent detection column)
Contract: docs/CONTRACTS.md §3.1
"""

import re
import secrets
import time
from collections import OrderedDict
from dataclasses import replace
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Form, HTTPException, UploadFile

from app import audit, config, evidence, transaction_state
from app.bank import readonly
from app.contracts import (
    NONE_OPTION_ID,
    Action,
    Answer,
    PreviewRequest,
    PreviewResponse,
    QuoteCondition,
    RequestPlan,
    TranscribeResponse,
    UnderstandRequest,
    UnderstandResponse,
)
from app.intent import asr, draft, gate, jev, options, session, shield

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

    One Jev call on the text with numbers swapped for stand-ins, cached by that normalised
    text, so retyping costs nothing.
    """
    if not config.MAKERS_MODELS_KEY:
        return _STUB_PREVIEW
    request = request.model_copy(update={"text": evidence.scrub(request.text)})
    # A fresh scope per preview: stand-ins and name pseudonyms can't be linked across requests
    # or people (D-049). The cache keys on the raw text, which stays in server memory, and on
    # the browser: a shared cache told anyone whether someone else had typed a text (H7).
    scope = f"preview|{secrets.token_hex(8)}"
    names = (
        options.aliases(readonly.get_accounts(request.user_id), scope) if request.user_id else {}
    )
    masked = options.mask(request.text, scope, names)
    key = f"{transaction_state.client.get()}|{request.user_id}|" + " ".join(
        request.text.lower().split()
    )
    if len(" ".join(masked.split())) < 2:
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
async def transcribe(audio: UploadFile, user_id: str = Form("u_demo")) -> dict[str, Any]:
    """Turn recorded speech into text via Tencent Cloud ASR (D-006, W-09).

    Multipart form: field ``audio`` (16 kHz mono 16-bit PCM WAV), optional
    ``user_id`` (default ``u_demo``). Returns ``{"transcript": str, "confidence": null}`` —
    the international-site API does not return a confidence score (CONTRACTS §3.1).
    """
    if not re.fullmatch(r"u_[A-Za-z0-9_-]{1,80}", user_id):
        raise HTTPException(400, "Invalid user ID.")
    data = await audio.read()
    try:
        result = await asr.transcribe(user_id, data)
    except asr.AsrError as error:
        raise HTTPException(error.status, error.message) from error
    _event(
        "transcribed",
        reason="ok" if result["transcript"] else "empty",
        count=len(result["transcript"].split()),
    )
    return result


@router.post("/understand", response_model=UnderstandResponse)
async def understand(request: UnderstandRequest) -> dict[str, Any]:
    """Turn a transcript, or the answer to a question, into the next step.

    A transcript with a known attack phrase is blocked before any model sees it. In a
    session that already holds a draft it is a follow-up turn about that draft;
    otherwise it starts a new command. Either way one Jev batch on the original text
    (numbers swapped for stand-ins) also carries the input shield's question; if Jev flags
    an injection, nothing else from that batch is used. An answer settles the open question
    from the stored replies without asking Jev again. A transcript sent while a question is
    open answers it instead (CONTRACTS §10.2).
    """
    readonly.get_accounts(request.user_id)  # an unknown user is a 404 before any state changes
    if request.transcript is not None:  # typed secrets go no further: Jev, validator, evidence
        request = request.model_copy(update={"transcript": evidence.scrub(request.transcript)})
    try:
        transaction_state.start_turn(request.user_id, request.session_id)
    except transaction_state.InvalidDraft as error:  # another client's conversation
        raise HTTPException(404, str(error)) from error
    if not _live():
        if request.transcript and options._CONDITIONAL.search(request.transcript):
            raise HTTPException(
                503, "Intent service unavailable. No conditional task was prepared."
            )
        return _stub_understand(request)
    started = time.monotonic()
    if request.transcript is not None:
        if shield.obvious(request.transcript):
            session.drop(request.session_id, request.user_id)
            return _blocked("", started)
        current = session.load(request.session_id, request.user_id)
        if current and current.pending:
            finished = await _spoken_answer(request, current, started)
            if finished is not None:
                return finished
        else:
            conditional = (
                options.conditional_parts(request.transcript, current.data) if current else None
            )
            if current and current.draft and not (conditional and conditional[1]):
                await _start_followup(current, request.transcript, request.session_id)
            else:
                current = await _start(request.user_id, request.transcript, request.session_id)
            flagged = shield.flagged(current.replies[0])
            _event("shield_checked", reason="flagged" if flagged else "clear")
            if flagged:
                session.drop(request.session_id, request.user_id)
                return _blocked(current.replies[0].model, started)
    else:
        current = session.load(request.session_id, request.user_id)
        if current is None:
            raise HTTPException(404, "This conversation has expired. Please start again.")
        if request.answer.option_id == NONE_OPTION_ID:
            session.drop(request.session_id, request.user_id)
            return _cancelled("user_declined", _meta(current, started))
        try:
            current.record(request.answer)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        _event("answer_received", reason="chip")
    if current.followup is not None:
        return await _followup_step(request, current, started)
    return _command_step(request, current, _meta(current, started))


async def _spoken_answer(
    request: UnderstandRequest, current: session.Session, started: float
) -> dict[str, Any] | None:
    """Read a spoken or typed reply to the open question (CONTRACTS §10.2).

    Records the pick and returns None so the caller carries on as if a chip was tapped;
    otherwise returns the finished reply (blocked, cancelled, a re-ask, or a fresh command).
    """
    question_id, name, offered = current.pending
    scope = f"{current.user_id}|{request.session_id}"
    question = options.spoken_reply_question(current.options, name, offered)
    masked = options.mask(request.transcript, scope, current.options.aliases)
    reply = await jev.ask(masked, [shield.QUESTION, question])
    meta = {
        "latency_ms": round((time.monotonic() - started) * 1000),
        "model": reply.model,
        "source": "jev",
        "phrasings": 1,
        "cached": False,
    }
    if shield.flagged(reply):
        session.drop(request.session_id, request.user_id)
        return _blocked(reply.model, started)
    p = gate.pick([reply], question.name)
    if not gate.trusted(p, _amount(current)):
        if name in {"plan_timing", "condition_currency"}:
            return {
                "status": "question",
                "question": {
                    "question_id": question_id,
                    "text": options._ASK[name],
                    "options": [
                        {"option_id": k, "label": v}
                        for k, v in current.options.labels[name].items()
                    ],
                    "allow_none": True,
                },
                "evidence": [],
                "meta": meta,
            }
        again = gate.Decision({name: gate.pick(current.replies, name)}, ask=name)
        return _ask(request, current, again, meta) or _unsupported(_HELP, meta)
    if p.option == options.NEW_REQUEST:
        fresh = await _start(current.user_id, request.transcript, request.session_id)
        if shield.flagged(fresh.replies[0]):
            session.drop(request.session_id, request.user_id)
            return _blocked(fresh.replies[0].model, started)
        return _command_step(request, fresh, _meta(fresh, started))
    if p.option not in offered:  # "none", or a "no" that wasn't one of the chips
        session.drop(request.session_id, request.user_id)
        return _cancelled("user_declined", meta)
    current.history.append({"kind": "spoken_answer", "text": request.transcript})
    current.record(Answer(question_id=question_id, option_id=p.option))
    _event("answer_received", reason="speech")
    return None


def _live() -> bool:
    # ponytail: stubs answer without a Jev key (CONTRACTS §7); delete them once every
    # machine that runs the app has one.
    return bool(config.MAKERS_MODELS_KEY)


async def _start(user_id: str, transcript: str, session_id: str) -> session.Session:
    try:
        data = options.UserData(readonly.get_accounts(user_id), readonly.list_quotes())
    except LookupError as error:
        raise HTTPException(404, "Unknown user.") from error
    conditional = options.conditional_parts(transcript, data)
    action_text = conditional[1] if conditional else transcript
    # Match the full instruction's stand-ins even when threshold and amount hashes collide.
    built = options.build(action_text, data, f"{user_id}|{session_id}", reference_text=transcript)
    built = replace(
        built,
        text=options.mask(transcript, f"{user_id}|{session_id}", built.aliases),
        questions=built.questions
        + [_CONSTRAINT_QUESTION, _COVERAGE_QUESTION]
        + (
            options.condition_questions(
                conditional[0]["text"], data, f"{user_id}|{session_id}", transcript
            )
            if conditional
            else []
        ),
    )
    evidence.record(
        "intent_context",
        {
            "accounts": data.accounts.model_dump(mode="json"),
            "quotes": [q.model_dump(mode="json") for q in data.quotes],
            "option_numbers": {k: str(v) for k, v in built.numbers.items()},
            "option_labels": built.labels,
        },
        session_id=session_id,
    )
    # Jev reads the text with stand-in numbers (D-026). W-04 rewrites that text after this
    # batch clears the shield, and rewrites must keep the stand-in numbers unchanged.
    phrasings = [built.text]
    replies = await jev.ask_all(phrasings, built.questions)
    return session.Session(
        user_id,
        transcript,
        built,
        data,
        replies,
        history=[{"kind": "instruction", "text": transcript}],
        condition=conditional[0] if conditional else None,
        action_text=action_text,
    )


async def _start_followup(current: session.Session, text: str, session_id: str) -> None:
    current.history.append({"kind": "followup", "text": text})
    scope = f"{current.user_id}|{session_id}"
    current.options = options.build_followup(text, current.draft, current.data, scope)
    current.options = replace(
        current.options,
        questions=current.options.questions + [_CONSTRAINT_QUESTION, _COVERAGE_QUESTION],
    )
    conditional = options.conditional_parts(text, current.data)
    current.condition = conditional[0] if conditional else None
    if conditional:
        current.options = replace(
            current.options,
            questions=current.options.questions
            + options.condition_questions(conditional[0]["text"], current.data, scope, text),
        )
    names = current.options.aliases
    context = {"current_tasks": [options.describe(a, names) for a in current.draft.actions]}
    current.replies = await jev.ask_all([current.options.text], current.options.questions, context)
    current.followup, current.answers, current.asked, current.pending = text, {}, 0, None


def _amount(current: session.Session) -> Decimal:
    # Non-transaction classification must not use an unrelated threshold or share count.
    if current.draft:
        values = []
        for action in current.draft.actions:
            if hasattr(action, "amount"):
                values.append(action.amount)
            elif action.type == "stock_order":
                quote = next((q for q in current.data.quotes if q.ticker == action.ticker), None)
                if quote:
                    values.append(quote.price * action.quantity)
        return max(values, default=Decimal(0))
    return Decimal(0)


def _command_step(
    request: UnderstandRequest, current: session.Session, meta: dict
) -> dict[str, Any]:
    _preserve_constraints(current)
    if current.condition is not None and not current.action_text:
        return _plan(request, current, [], meta, ["The requested action needs clarification."])
    # Only a confidently read-only command may bypass transaction field resolution.
    decision = gate.decide(
        current.replies, current.answers, _amount(current), current.options, current.data
    )
    _event("gate_decision", reason="ask" if decision.ask else "proceed")
    if current.condition is None and not (decision.ask or decision.missing or decision.actions):
        answered = _answer_step(request, current, meta)
        if answered:
            return answered
    if decision.ask:
        question = _ask(request, current, decision, meta)
        if question:
            return question
        decision = gate.Decision(decision.picks, decision.actions, missing=decision.ask)
    if decision.missing or not decision.actions:
        if current.condition is not None:
            return _plan(request, current, [], meta, [_MISSING.get(decision.missing, _HELP)])
        session.drop(request.session_id, request.user_id)
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
        if current.condition is not None:
            return _plan(request, current, [], meta, [str(error)])
        session.drop(request.session_id, request.user_id)
        return _unsupported(str(error), meta)
    # The objective only shapes the screen on a first command, so an unsure one means "prepare".
    p = gate.pick(current.replies, "objective")
    objective = p.option if gate.trusted(p, _amount(current)) else "prepare"
    return _keep_draft(request, current, built, objective, None, meta)


# The extra picks each read-only kind needs (CONTRACTS §3.1 answers).
_ANSWER_SLOTS = {
    "balance": ["balance_account"],
    "transactions": [
        "transactions_account",
        "transactions_period",
        "transactions_payee",
        "transactions_direction",
        "transactions_order",
    ],
    "bills": ["bills_scope"],
    "quote": ["ticker"],
    "fx_rate": ["fx_pair"],
}


def _answer_step(request: UnderstandRequest, current: session.Session, meta: dict) -> dict | None:
    """Answer a question that moves no money (CONTRACTS §3.1 answers, v0.6 proposed).

    Jev only says what is asked and which account, period, ticker or pair; code reads values.
    """
    names = ["info"]
    step = gate.decide_each(current.replies, current.answers, _amount(current), names)
    if not step.ask:
        kind = step.picks["info"].option
        if kind == "none":
            return None
        names = _ANSWER_SLOTS.get(kind, [])
        step = gate.decide_each(current.replies, current.answers, _amount(current), names)
    if step.ask:
        return _ask(request, current, step, meta)
    picks = {name: step.picks[name].option for name in names}
    try:
        payload = draft.answer(kind, picks, current.data, current.user_id, current.transcript)
    except draft.Unbuildable as error:
        if current.condition is not None:
            return _plan(request, current, [], meta, [str(error)])
        session.drop(request.session_id, request.user_id)
        return _unsupported(str(error), meta)
    except readonly.ForecastDataError:
        session.drop(request.session_id, request.user_id)
        return _unsupported("I can't read your upcoming bills right now.", meta)
    session.drop(request.session_id, request.user_id)
    _event("answer_given", reason=kind)
    return {
        "status": "answer",
        "kind": kind,
        **payload,
        "meta": meta,
        "presentation": _presentation(current, request.session_id, [kind]),
    }


async def _followup_step(
    request: UnderstandRequest, current: session.Session, started: float
) -> dict[str, Any]:
    meta = _meta(current, started)
    _preserve_constraints(current)
    if current.condition is not None:
        if options._ACTION_START.search(current.followup):
            return _plan(request, current, [], meta, ["The revised action needs clarification."])
        return _plan(request, current, current.draft.actions, meta, [])
    first = gate.decide_each(current.replies, current.answers, _amount(current), ["objective"])
    if first.ask:
        return _ask(request, current, first, meta) or _unsupported(_HELP, meta)
    objective = first.picks["objective"].option
    if objective == "new_task":
        fresh = await _start(current.user_id, current.followup, request.session_id)
        return _command_step(request, fresh, _meta(fresh, started))
    skip = {"objective", shield.QUESTION.name, "execution_constraint", "request_coverage"}
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
    # Chips come from the unmerged answers, so an account the gate counted as "default" shows.
    unmerged = gate.pick(current.replies, decision.ask).probabilities
    text, chips = options.clarify(current.options, decision.ask, unmerged)
    if not chips:
        return None
    if current.asked >= config.MAX_QUESTIONS:
        session.drop(request.session_id, request.user_id)
        return _cancelled("too_many_questions", meta)
    current.asked += 1
    question_id = f"q_{current.asked}"
    _event("question_asked", reason=decision.ask, count=current.asked)
    current.history.append(
        {
            "kind": "question",
            "question_id": question_id,
            "question": decision.ask,
            "text": text,
            "options": [{"option_id": o, "label": label} for o, label in chips],
        }
    )
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


def _validator_facts(built, current: session.Session) -> dict[str, Any]:
    """The bank data the validator may need for this draft, and no more (D-048).

    Balances only for the accounts the draft uses; billers, holdings and quotes only for what
    it names. Payee names stay, so an ambiguous "John" is still visible. On a first command it
    also records which spoken number each amount came from (Jayanth's item 4).
    """
    used = {a.from_account_id for a in built.actions} | {
        getattr(a, "to_account_id", None) for a in built.actions
    }
    tickers = {a.ticker for a in built.actions if a.type == "stock_order"}
    billers = {a.biller_id for a in built.actions if a.type == "bill_payment"}
    accounts = current.data.accounts
    facts: dict[str, Any] = {
        "accounts": {
            "accounts": [
                a.model_dump(mode="json") for a in accounts.accounts if a.account_id in used
            ],
            "payees": [p.model_dump(mode="json") for p in accounts.payees],
            "billers": [
                {
                    **b.model_dump(mode="json"),
                    "bill_reference": readonly.get_bill_reference(b.biller_id),
                    "currency": "SGD",
                }
                for b in accounts.billers
                if b.biller_id in billers
            ],
            "holdings": [
                h.model_dump(mode="json") for h in accounts.holdings if h.ticker in tickers
            ],
        },
        "quotes": [q.model_dump(mode="json") for q in current.data.quotes if q.ticker in tickers],
        "fx_rates": [],
    }
    # Preserve account choices without exposing unused balances. Defaults must come from
    # the full original bank list, never from the model-selected account subset.
    facts["account_options"] = [
        a.model_dump(mode="json", exclude={"balance"}) for a in accounts.accounts
    ]
    facts["defaults"] = {
        "transfer.currency": "SGD",
        "bill_payment.currency": "SGD",
        "stock_order.order_type": "market",
        "stock_order.limit_price": "null",
    }
    if accounts.accounts:
        source = next(
            (a for a in accounts.accounts if "savings" in a.type.lower()), accounts.accounts[0]
        )
        facts["defaults"].update(
            {
                "transfer.from_account_id": source.account_id,
                "bill_payment.from_account_id": source.account_id,
            }
        )
    if current.followup is None:  # a follow-up's evidence mixes turns, so only first commands
        said = current.options.said
        picks = {
            e.question: "n" + e.pick[1:] if e.pick[:1] == "m" else e.pick for e in built.evidence
        }
        facts["amounts_said"] = {  # "m1" (a sum for shares) is the same number as "n1"
            question: {
                "phrase": said[oid].phrase,
                "value": str(said[oid].value),
                "currency": said[oid].currency,
                "account_id": said[oid].account_id,
                "the_rest": said[oid].remaining,
            }
            for question, oid in picks.items()
            if oid in said
        }
    return facts


def _keep_draft(request, current, built, objective, scenario, meta) -> dict[str, Any]:
    if current.condition is not None and built.condition is None:
        return _plan(request, current, built.actions, meta, [], built=built)
    # The session stays open after a draft so follow-up turns can amend it (CONTRACTS §3.1).
    facts = _validator_facts(built, current)
    for action in built.actions:
        if action.type == "fx_conversion":
            try:
                facts["fx_rates"].append(
                    readonly.get_fx_rate(action.sell_currency, action.buy_currency).model_dump(
                        mode="json", by_alias=True
                    )
                )
            except LookupError:
                pass
    try:
        transaction_state.register(built, current.history, facts)
    except transaction_state.InvalidDraft as error:
        raise HTTPException(409, str(error)) from error
    current.draft, current.followup, current.pending = built, None, None
    session.save(request.session_id, current)
    _event("draft_built", draft_id=built.draft_id, count=len(built.actions))
    return {
        "status": "draft",
        "draft": built,
        "objective": objective,
        "scenario": scenario,
        "presentation": _presentation(current, request.session_id, [a.type for a in built.actions]),
        "meta": meta,
    }


def _event(event: str, **data: Any) -> None:
    """Write one intent event to the audit chain (CONTRACTS §8).

    The chain takes only allowlisted, non-sensitive fields (app/audit.py), so transcripts and
    probabilities stay in the encrypted evidence store. `GET /api/audit` is public and a
    session ID unlocks its conversation, so session IDs stay out of the chain. A failed
    write stops the request (503).
    """
    audit.append_event("intent", event, data)


def _blocked(model: str, started: float) -> dict[str, Any]:
    # No model means the phrase backstop blocked it before any Jev call.
    _event("blocked", reason="possible_injection")
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


_HELP = "I can help with transfers, bill payments, stock orders and questions about your accounts."
_BLOCKED = "This request contains instructions I can't act on. Please say it again."
_MISSING = {
    "transfer_amount": 'Please say how much to send, for example "send fifty to John".',
    "quantity": (
        'Please say how many shares or how much to invest, for example "buy 100 Apple shares" '
        'or "invest 2k in Apple".'
    ),
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
    _event("cancelled", reason=reason)
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


_CONSTRAINT_QUESTION = jev.Question(
    "execution_constraint",
    "Does the instruction restrict when or whether a task may happen? Preserve every condition. "
    "A question about affordability alone is not a conditional instruction.",
    {
        "no": "Ordinary immediate task or read-only question, no execution condition.",
        "conditional": "Task depends on a fact, event or threshold, including implicit conditions.",
        "deferred": "Task must happen later, on a schedule, or automatically in the future.",
    },
)


def _preserve_constraints(current: session.Session) -> None:
    if current.condition is not None:
        return
    p = gate.pick(current.replies, "execution_constraint")
    if p.option != "no" or not gate.trusted(p, _amount(current)):
        current.condition = {"text": current.followup or current.transcript, "state": "unknown"}
        current.action_text = ""


_COVERAGE_QUESTION = jev.Question(
    "request_coverage",
    "Does the request need information beyond the structured capabilities? Supported: "
    "transfers, bills, stock orders, own-account moves, FX; balances, transactions, upcoming "
    "bills, holdings, quotes, FX rates, payees; single immediate stock-price conditions. "
    "Missing amounts, ambiguous recipients and ordinary amendments are NOT extra UI needs. "
    "Do not mark partial merely because a field needs clarification.",
    {
        "complete": "The request fits these structured capabilities.",
        "spending": "Also needs spending analysis or month-to-month comparisons.",
        "cashflow": "Also needs future cashflow or scheduled-income analysis.",
        "other": "An explicit requirement lies outside these structured capabilities.",
    },
)


def _presentation(current: session.Session, session_id: str, tasks: list[str]) -> dict:
    result = options.presentation_intent(
        current.transcript,
        current.history,
        current.data,
        f"{current.user_id}|{session_id}",
        tasks,
        [current.condition["text"]] if current.condition else [],
    )
    gaps = []
    if all("request_coverage" in r.answers for r in current.replies):
        coverage = gate.pick(current.replies, "request_coverage")
        if coverage.option != "complete" and gate.trusted(coverage, Decimal("0")):
            gaps.append(coverage.option)
    # Compare independently selected requirements with the output actually produced.
    for action in options.ACTION_TYPES:
        name = f"has_{action}"
        if action not in tasks and all(name in r.answers for r in current.replies):
            pick = gate.pick(current.replies, name)
            if pick.option == "yes" and gate.trusted(pick, Decimal("0")):
                gaps.append(action)
    if all("info" in r.answers for r in current.replies):
        pick = gate.pick(current.replies, "info")
        if pick.option not in {"none", "help", *tasks} and gate.trusted(pick, Decimal("0")):
            # The condition card already represents its quote dependency.
            if not (pick.option == "quote" and current.condition):
                gaps.append(pick.option)
    return {**result, "coverage": "partial" if gaps else "complete", "gaps": gaps[:8]}


def _plan(
    request: UnderstandRequest,
    current: session.Session,
    actions: list[Action],
    meta: dict[str, Any],
    unresolved: list[str],
    built=None,
) -> dict:
    """Only a met, current predicate can be attached to a signable draft."""
    text = current.followup or current.transcript
    condition = dict(current.condition)
    names = ["condition_shape", "condition_asset", "condition_operator", "condition_threshold"]
    if all(name in current.replies[0].answers for name in names):
        picks = [gate.pick(current.replies, name) for name in names]
        numbers = options.parse_numbers(condition["text"])
        condition = {"text": condition["text"], "state": "unknown"}
        if (
            all(gate.trusted(p, _amount(current)) and p.option != "none" for p in picks)
            and picks[0].option == "single"
            and len(numbers) == 1
            and numbers[0].value.as_tuple().exponent >= -2
            and picks[3].option == "c0"
        ):
            condition.update(
                ticker=picks[1].option,
                operator=picks[2].option,
                threshold=f"{numbers[0].value:.2f}",
                currency=numbers[0].currency,
            )
    if "condition_currency" in current.answers:
        condition["currency"] = current.answers["condition_currency"]
    if all(condition.get(k) is not None for k in ["ticker", "operator", "threshold", "currency"]):
        predicate = QuoteCondition.model_validate(
            {k: condition[k] for k in ["ticker", "operator", "threshold", "currency"]}
        )
        condition.update(readonly.evaluate_condition(predicate))
    current.condition = condition
    quote = condition.get("quote")
    if (
        quote
        and quote["currency"] != condition.get("currency")
        and "condition_currency" not in current.answers
    ):
        labels = {"USD": "US dollars (USD)", "SGD": "Singapore dollars (SGD)"}
        name = "condition_currency"
        current.options.labels[name] = labels
        if not any(q.name == name for q in current.options.questions):
            current.options.questions.append(jev.Question(name, options._ASK[name], labels))
        current.pending = ("q_condition_currency", name, set(labels))
        current.history.append(
            {
                "kind": "question",
                "question_id": "q_condition_currency",
                "question": name,
                "text": options._ASK[name],
            }
        )
        session.save(request.session_id, current)
        return {
            "status": "question",
            "question": {
                "question_id": "q_condition_currency",
                "text": options._ASK[name],
                "options": [{"option_id": k, "label": v} for k, v in labels.items()],
                "allow_none": True,
            },
            "evidence": [],
            "meta": meta,
        }
    timing = current.answers.get("plan_timing")
    if not timing and re.search(r"\b(watch|monitor|later|automatically)\b", text, re.I):
        timing = "watch"
    if not timing and re.search(r"\b(now|immediately)\b", text, re.I):
        timing = "now"
    if not timing and actions:
        labels = {"now": "Check now", "watch": "Watch for it later"}
        current.options.labels["plan_timing"] = labels
        if not any(q.name == "plan_timing" for q in current.options.questions):
            current.options.questions.append(
                jev.Question("plan_timing", "When should this be checked?", labels)
            )
        current.pending = ("q_plan_timing", "plan_timing", set(labels))
        current.history.append(
            {
                "kind": "question",
                "question_id": "q_plan_timing",
                "question": "plan_timing",
                "text": "Check this now, or watch for it later?",
            }
        )
        session.save(request.session_id, current)
        return {
            "status": "question",
            "question": {
                "question_id": "q_plan_timing",
                "text": "Check this now, or watch for it later?",
                "options": [{"option_id": k, "label": v} for k, v in labels.items()],
                "allow_none": True,
            },
            "evidence": [],
            "meta": meta,
        }
    if timing == "now" and condition.get("state") == "met" and actions and not unresolved:
        candidate = built or current.draft
        if candidate is not None:
            candidate = candidate.model_copy(update={"condition": predicate, "actions": actions})
            return _keep_draft(request, current, candidate, "prepare", None, meta)
    limitation = (
        "Automatic conditional transfers aren't available. Nothing is scheduled."
        if timing == "watch"
        else {
            "not_met": "Condition not met. Nothing will be sent.",
            "stale": "A fresh quote is needed before you can transfer.",
            "unknown": "The condition needs clarification or matching currency data.",
        }.get(condition.get("state"), "This request cannot be prepared yet.")
    )
    plan = {
        "request": text,
        "condition": current.condition,
        "actions": actions,
        "timing": timing or "unspecified",
        "unresolved": unresolved,
        "executable": False,
        "limitation": limitation,
        "intent": _presentation(current, request.session_id, [a.type for a in actions]),
    }
    plan = RequestPlan.model_validate(plan).model_dump(mode="json")
    session.drop(request.session_id, request.user_id)
    evidence.record("intent_plan", {"plan": plan}, session_id=request.session_id)
    return {"status": "plan", "plan": plan, "meta": meta}
