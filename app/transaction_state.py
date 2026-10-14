"""Shared draft authorization state; no model or bank write imports.

Single-process demo store, like intent sessions. Restart invalidates all receipts.
Only server-produced drafts may enter. Locks protect result/revision transitions.
"""

import copy
import hashlib
import hmac
import json
import secrets
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass

from app import config
from app.contracts import Draft


class InvalidDraft(ValueError):
    pass


@dataclass
class Record:
    draft: Draft
    digest: str
    revision: str
    expires: float
    history: list[dict]
    facts: dict
    result: dict | None = None
    checking: bool = False
    started: float = 0


_lock = threading.RLock()
_records: dict[tuple[str, str], Record] = {}
_turns: dict[tuple[str, str], tuple[str, float]] = {}
_turn: ContextVar[tuple[tuple[str, str], str] | None] = ContextVar("audit_turn", default=None)


def start_turn(user_id: str, session_id: str):
    with _lock:
        now = time.monotonic()
        for key in [k for k, (_, expires) in _turns.items() if expires < now]:
            _turns.pop(key)
        if len(_turns) >= 1000 and (user_id, session_id) not in _turns:
            raise InvalidDraft("Too many active conversations.")
        key = (user_id, session_id)
        token = secrets.token_hex(16)
        _turns[key] = (token, now + config.SESSION_TIMEOUT_SECONDS)
        _turn.set((key, token))
        revoke(user_id, session_id)


def canonical(draft: Draft) -> str:
    return json.dumps(
        draft.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def digest(draft: Draft) -> str:
    return hashlib.sha256(canonical(draft).encode()).hexdigest()


def clear():
    with _lock:
        _records.clear()
        _turns.clear()
        _turn.set(None)


def revoke(user_id: str, session_id: str):
    with _lock:
        _records.pop((user_id, session_id), None)


def register(draft: Draft, history: list[dict], facts: dict):
    with _lock:
        now = time.monotonic()
        turn = _turn.get()
        if turn is not None and (_turns.get(turn[0], (None, 0))[0] != turn[1]):
            raise InvalidDraft("A newer instruction superseded this request.")
        for key in [k for k, r in _records.items() if r.expires < now]:
            del _records[key]
        if len(_records) >= 1000 and (draft.user_id, draft.session_id) not in _records:
            raise InvalidDraft("Too many pending requests. Try again later.")
        _records[(draft.user_id, draft.session_id)] = Record(
            draft.model_copy(deep=True),
            digest(draft),
            secrets.token_hex(16),
            now + config.SESSION_TIMEOUT_SECONDS,
            copy.deepcopy(history),
            copy.deepcopy(facts),
        )


def _current(draft: Draft) -> Record:
    r = _records.get((draft.user_id, draft.session_id))
    if r is None or r.expires <= time.monotonic() or r.digest != digest(draft):
        raise InvalidDraft("This draft changed or expired. Request a fresh draft.")
    return r


def begin(draft: Draft) -> tuple[Record, dict | None]:
    with _lock:
        r = _current(draft)
        if r.result and r.result["status"] != "unavailable":
            return r, copy.deepcopy(r.result)
        if r.checking:
            return r, {"status": "pending", "message": "Checking your request."}
        r.checking, r.started = True, time.monotonic()
        return r, None


def _receipt(r: Record) -> str:
    message = f"{r.revision}.{r.digest}"
    mac = hmac.new(
        config.VALIDATION_RECEIPT_KEY.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return f"{message}.{mac}"


def finish(r: Record, result: dict) -> dict:
    with _lock:
        current = _records.get((r.draft.user_id, r.draft.session_id))
        if current is not r or r.expires <= time.monotonic():
            return {"status": "stale", "message": "This draft changed or expired."}
        r.checking = False
        result = copy.deepcopy(result)
        if result["status"] == "match":
            result["receipt"] = _receipt(r)
        r.result = result
        return copy.deepcopy(result)


def authorize(draft: Draft, receipt: str) -> Record:
    with _lock:
        r = _current(draft)
        if (
            not r.result
            or r.result["status"] != "match"
            or not hmac.compare_digest(_receipt(r), receipt)
        ):
            raise InvalidDraft("This draft has not passed independent validation.")
        return r


def cancel(draft: Draft):
    """A late cancellation for an old payload must not revoke its replacement."""
    with _lock:
        try:
            _current(draft)
        except InvalidDraft:
            return
        revoke(draft.user_id, draft.session_id)
