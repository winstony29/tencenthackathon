"""Encrypted evidence and a persistent, hash-chained request timeline.

Owner: shared
Contract: docs/CONTRACTS.md, audit evidence extension
"""

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app import config
from app.audit import AuditError

context: ContextVar[dict | None] = ContextVar("audit_context", default=None)
_SECRET = re.compile(
    r"authorization|cookie|password|secret|api.?key|private.?key|otp|receipt|token|credential|^d$|^p$|^q$|^dp$|^dq$|^qi$",
    re.I,
)


def clean(value):
    """Remove secret fields and configured credential values, including free text."""
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if _SECRET.search(str(k)) else clean(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        for name, secret in vars(config).items():
            if (
                (_SECRET.search(name) or name.endswith(("_KEY", "_TOKEN")))
                and isinstance(secret, str)
                and len(secret) >= 8
            ):
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return value


def encoded(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _cipher() -> Fernet:
    try:
        return Fernet(config.AUDIT_EVIDENCE_KEY.encode())
    except (ValueError, TypeError):
        raise AuditError("Audit evidence encryption is not configured.") from None


@contextmanager
def _connect():
    path = (
        Path(config.AUDIT_EVIDENCE_PATH)
        if config.AUDIT_EVIDENCE_PATH
        else (Path(__file__).resolve().parents[1] / "var" / "evidence.sqlite3")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA secure_delete=ON")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        position INTEGER PRIMARY KEY, metadata TEXT NOT NULL, hash TEXT NOT NULL,
        payload BLOB, expires REAL NOT NULL)""")
    try:
        with db:
            yield db
    finally:
        db.close()


def _verified(db):
    previous = "0" * 64
    rows = db.execute("SELECT position,metadata,hash,payload,expires FROM events ORDER BY position")
    result = []
    for position, text, digest, payload, expires in rows:
        meta = json.loads(text)
        if (
            position != len(result)
            or meta["position"] != position
            or meta["expires_at"] != expires
            or meta["previous_hash"] != previous
            or (payload is None and expires > time.time())
            or sha(encoded(meta)) != digest
        ):
            raise AuditError("Evidence timeline integrity check failed.")
        previous = digest
        result.append((meta, digest, payload, expires))
    return result


def record(event: str, payload: dict, **links) -> str:
    """Persist before returning success; no credentials in metadata or encrypted evidence."""
    try:
        cipher = _cipher()
        body = encoded(clean(payload))
        if len(body) > config.AUDIT_MAX_BYTES:
            raise AuditError("Audit evidence exceeds the size limit.")
        binding = {**(context.get() or {}), **links}
        # Store caller-provided identifiers only inside encrypted evidence if malformed.
        binding = {
            k: v
            for k, v in binding.items()
            if k in {"request_id", "session_id", "draft_id", "revision", "draft_digest"}
            and isinstance(v, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v)
        }
        identifier = "ev_" + uuid.uuid4().hex
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = _verified(db)
            now = time.time()
            db.execute(
                "UPDATE events SET payload=NULL WHERE expires<=? AND payload IS NOT NULL", (now,)
            )
            expires = now + max(1, config.AUDIT_RETENTION_DAYS) * 86400
            meta = {
                "position": len(rows),
                "event_id": identifier,
                "event": event,
                "time": now,
                "expires_at": expires,
                **binding,
                "evidence_digest": sha(body),
                "previous_hash": rows[-1][1] if rows else "0" * 64,
            }
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (
                    len(rows),
                    encoded(meta).decode(),
                    sha(encoded(meta)),
                    cipher.encrypt(body),
                    expires,
                ),
            )
        return identifier
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise AuditError("Could not persist audit evidence.") from error


def timeline(session_id: str | None, limit: int, include_evidence: bool) -> dict:
    """Verify the chain and payload digests; expiry never silently becomes an empty record."""
    try:
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = _verified(db)
            db.execute("UPDATE events SET payload=NULL WHERE expires<=?", (time.time(),))
            rows = _verified(db)
            selected = [
                r for r in rows if session_id is None or r[0].get("session_id") == session_id
            ]
            entries = []
            for meta, digest, blob, _ in selected[-limit:]:
                item = {
                    **meta,
                    "hash": digest,
                    "evidence_state": "expired" if blob is None else "retained",
                }
                if blob is not None and include_evidence:
                    body = _cipher().decrypt(blob)
                    if sha(body) != meta["evidence_digest"]:
                        raise AuditError("Evidence digest mismatch.")
                    item["evidence"] = json.loads(body)
                entries.append(item)
            return {"chain_valid": True, "entries": entries}
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, InvalidToken) as error:
        raise AuditError("Audit evidence is unavailable or corrupted.") from error


def purge_expired() -> None:
    """Run on startup and hourly; retain integrity metadata after evidence expiry."""
    try:
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _verified(db)
            db.execute("UPDATE events SET payload=NULL WHERE expires<=?", (time.time(),))
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise AuditError("Audit retention maintenance failed.") from error
