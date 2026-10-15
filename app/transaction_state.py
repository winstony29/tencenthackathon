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

from app import config, evidence
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
    client: str = ""


_lock = threading.RLock()
_records: dict[tuple[str, str], Record] = {}
_turns: dict[tuple[str, str], tuple[str, float]] = {}
_turn: ContextVar[tuple[tuple[str, str], str] | None] = ContextVar("audit_turn", default=None)
# The browser's server-issued client secret (HttpOnly cookie, app/main.py). A session ID is
# chosen by the client and can leak, so each conversation belongs to the client that started it.
client: ContextVar[str] = ContextVar("client", default="")
_owners: dict[str, tuple[str, float]] = {}  # session_id -> (client, expires)


def start_turn(user_id: str, session_id: str):
    """Open a new turn; refuse a session ID that another client is using (InvalidDraft)."""
    with _lock:
        now = time.monotonic()
        for key in [k for k, (_, expires) in _turns.items() if expires < now]:
            _turns.pop(key)
        for key in [k for k, (_, expires) in _owners.items() if expires < now]:
            _owners.pop(key)
        owner = _owners.get(session_id)
        if owner is not None and not hmac.compare_digest(owner[0], client.get()):
            raise InvalidDraft("This conversation has expired. Please start again.")
        if len(_owners) >= 1000 and owner is None:
            _owners.pop(min(_owners, key=lambda k: _owners[k][1]))
        _owners[session_id] = (client.get(), now + config.SESSION_TIMEOUT_SECONDS)
        if len(_turns) >= 1000 and (user_id, session_id) not in _turns:
            # Evict the conversation closest to expiry: junk sessions can't lock everyone out.
            oldest = min(_turns, key=lambda k: _turns[k][1])
            _turns.pop(oldest)
            revoke(*oldest)
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
        _owners.clear()
        _turn.set(None)


def revoke(user_id: str, session_id: str):
    with _lock:
        old = _records.pop((user_id, session_id), None)
        if old:
            evidence.record(
                "draft_revoked",
                {},
                session_id=session_id,
                draft_id=old.draft.draft_id,
                revision=old.revision,
                draft_digest=old.digest,
            )


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
        record = Record(
            draft.model_copy(deep=True),
            digest(draft),
            secrets.token_hex(16),
            now + config.SESSION_TIMEOUT_SECONDS,
            copy.deepcopy(history),
            copy.deepcopy(facts),
            client=client.get(),
        )
        evidence.record(
            "draft_registered",
            {
                "draft": draft.model_dump(mode="json"),
                "history": history,
                "bank_snapshot": facts,
            },
            session_id=draft.session_id,
            draft_id=draft.draft_id,
            revision=record.revision,
            draft_digest=record.digest,
        )
        _records[(draft.user_id, draft.session_id)] = record


def _current(draft: Draft) -> Record:
    r = _records.get((draft.user_id, draft.session_id))
    if (
        r is None
        or r.expires <= time.monotonic()
        or r.digest != digest(draft)
        or not hmac.compare_digest(r.client, client.get())
    ):
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
            evidence.record(
                "validation_discarded",
                {"reason": "stale", "result": result},
                session_id=r.draft.session_id,
                draft_id=r.draft.draft_id,
                revision=r.revision,
                draft_digest=r.digest,
            )
            return {"status": "stale", "message": "This draft changed or expired."}
        r.checking = False
        result = copy.deepcopy(result)
        if result["status"] == "match":
            result["receipt"] = _receipt(r)
        evidence.record(
            "validation_applied",
            {"result": result},
            session_id=r.draft.session_id,
            draft_id=r.draft.draft_id,
            revision=r.revision,
            draft_digest=r.digest,
        )
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
