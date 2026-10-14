"""Shared draft authorization state; no model or bank write imports.

Encrypted persistent approval state when bank execution is enabled.
Only server-produced drafts may enter. Locks protect result/revision transitions.
"""

import base64
import copy
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

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
    issued: float = 0


_lock = threading.RLock()
_records: dict[tuple[str, str], Record] = {}
_turns: dict[tuple[str, str], tuple[str, float]] = {}
_turn: ContextVar[tuple[tuple[str, str], str] | None] = ContextVar("audit_turn", default=None)


def start_turn(user_id: str, session_id: str):
    with _locked():
        now = time.time()
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
    with _locked():
        _records.clear()
        _turns.clear()
        _turn.set(None)


def revoke(user_id: str, session_id: str):
    with _locked():
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
    with _locked():
        now = time.time()
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
            issued=now,
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
    if r is None or r.expires <= time.time() or r.digest != digest(draft):
        raise InvalidDraft("This draft changed or expired. Request a fresh draft.")
    return r


def begin(draft: Draft) -> tuple[Record, dict | None]:
    with _locked():
        r = _current(draft)
        if r.result and r.result["status"] != "unavailable":
            return r, copy.deepcopy(r.result)
        if r.checking:
            return r, {"status": "pending", "message": "Checking your request."}
        r.checking, r.started = True, time.time()
        return r, None


def _claims(r: Record) -> dict:
    return {
        "user_id": r.draft.user_id,
        "session_id": r.draft.session_id,
        "draft_id": r.draft.draft_id,
        "context_id": r.revision,
        "revision": r.revision,
        "logical_id": r.draft.session_id,
        "draft_hash": r.digest,
        "expires_at": datetime.fromtimestamp(r.expires, UTC).isoformat().replace("+00:00", "Z"),
    }


def _receipt(r: Record) -> str:
    claims = {
        "version": 1,
        "audience": "dcta.transaction.validation",
        **_claims(r),
        "issued_at": datetime.fromtimestamp(r.issued, UTC).isoformat().replace("+00:00", "Z"),
    }
    body = evidence.encoded(claims)
    try:
        key = base64.urlsafe_b64decode(
            config.VALIDATION_RECEIPT_KEY + "=" * (-len(config.VALIDATION_RECEIPT_KEY) % 4)
        )
        if len(key) < 32:
            raise ValueError
    except ValueError:
        raise InvalidDraft("Validation signing key is not configured.") from None
    mac = hmac.digest(key, b"DCTA-VALIDATION-RECEIPT\0v1\0" + body, "sha256")

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    return "vr_1." + encode(body) + "." + encode(mac)


def finish(r: Record, result: dict) -> dict:
    with _locked():
        current = _records.get((r.draft.user_id, r.draft.session_id))
        if current is None or current.revision != r.revision or r.expires <= time.time():
            evidence.record(
                "validation_discarded",
                {"reason": "stale", "result": result},
                session_id=r.draft.session_id,
                draft_id=r.draft.draft_id,
                revision=r.revision,
                draft_digest=r.digest,
            )
            return {"status": "stale", "message": "This draft changed or expired."}
        r = current
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
    with _locked():
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
    with _locked():
        try:
            _current(draft)
        except InvalidDraft:
            return
        revoke(draft.user_id, draft.session_id)


_local = threading.local()


@contextmanager
def _locked():
    """Lease authoritative revisions across processes; evidence uses a separate database."""
    with _lock:
        if not config.BANK_EXECUTION_ENABLED or getattr(_local, "active", False):
            yield
            return
        path = (
            Path(config.TRANSACTION_STATE_PATH)
            if config.TRANSACTION_STATE_PATH
            else Path(__file__).resolve().parents[1] / "var" / "approvals.sqlite3"
        )
        evidence_path = (
            Path(config.AUDIT_EVIDENCE_PATH)
            if config.AUDIT_EVIDENCE_PATH
            else Path(__file__).resolve().parents[1] / "var" / "evidence.sqlite3"
        )
        if path.resolve() == evidence_path.resolve():
            raise InvalidDraft("Approval and evidence storage must be separate.")
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.execute(
                "CREATE TABLE IF NOT EXISTS approvals "
                "(id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM approvals WHERE id=1").fetchone()
            _records.clear()
            _turns.clear()
            if row:
                body = json.loads(evidence._cipher().decrypt(row[0]))
                for raw in body["records"]:
                    raw["draft"] = Draft.model_validate(raw["draft"])
                    record = Record(**raw)
                    if record.expires > time.time():
                        _records[(record.draft.user_id, record.draft.session_id)] = record
                _turns.update({tuple(key): tuple(value) for key, value in body["turns"]})
            _local.active = True
            try:
                yield
            finally:
                # Revocation must survive even if its evidence write failed.
                records = []
                for record in _records.values():
                    raw = asdict(record)
                    raw["draft"] = record.draft.model_dump(mode="json")
                    records.append(raw)
                payload = evidence._cipher().encrypt(
                    evidence.encoded({"records": records, "turns": list(_turns.items())})
                )
                db.execute("INSERT OR REPLACE INTO approvals VALUES (1,?)", (payload,))
                db.commit()
                _local.active = False
        except Exception:
            db.rollback()
            raise
        finally:
            _local.active = False
            db.close()


@contextmanager
def execution_context(draft: Draft):
    """A supervised local customer's validated revision, held until execution ends."""
    if not config.BANK_EXECUTION_ENABLED or draft.user_id != config.BANK_SUPERVISED_USER:
        raise InvalidDraft("Customer execution context is unavailable.")
    with _locked():
        record = _current(draft)
        if not record.result or record.result.get("status") != "match":
            raise InvalidDraft("This draft has not passed independent validation.")
        yield _claims(record)
