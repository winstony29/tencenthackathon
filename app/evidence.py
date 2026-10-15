"""Encrypted evidence and a persistent, hash-chained request timeline.

Owner: shared
Contract: docs/CONTRACTS.md, audit evidence extension
"""

import hashlib
import hmac
import json
import os
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


def _mac(label: str, value: bytes) -> str:
    """Keyed digest for plaintext metadata: a plain SHA-256 of a short request lets anyone
    holding the file confirm guesses such as "send 350 to John Doe" (H6, 15 Oct audit)."""
    key = hmac.new(config.AUDIT_EVIDENCE_KEY.encode(), label.encode(), hashlib.sha256).digest()
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def _link(meta: dict) -> str:
    """Keyed chain hash: rewriting history needs the evidence key, not just file access."""
    key = hashlib.sha256(b"dcta-evidence-chain:" + config.AUDIT_EVIDENCE_KEY.encode()).digest()
    return hmac.new(key, encoded(meta), hashlib.sha256).hexdigest()


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
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)  # transcripts and bank snapshots, even encrypted, are owner-only
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


def _check(meta: dict, digest: str, position: int, previous: str, empty: bool, expires: float):
    if (
        meta["position"] != position
        or meta["expires_at"] != expires
        or meta["previous_hash"] != previous
        # Evidence may be absent only after expiry, or when capacity dropped it on write.
        or (empty and expires > time.time() and not meta.get("dropped"))
        or not hmac.compare_digest(_link(meta), digest)
    ):
        raise AuditError("Evidence timeline integrity check failed.")


def _verified(db):
    """Check the whole chain; reads metadata only, never the encrypted payloads."""
    previous = "0" * 64
    rows = db.execute(
        "SELECT position,metadata,hash,payload IS NULL,expires FROM events ORDER BY position"
    )
    result = []
    for position, text, digest, empty, expires in rows:
        meta = json.loads(text)
        if position != len(result):
            raise AuditError("Evidence timeline integrity check failed.")
        _check(meta, digest, position, previous, empty, expires)
        previous = digest
        result.append((meta, digest, not empty, expires))
    return result


def _last(db) -> tuple[int, str]:
    """Check only the newest link before appending; the full chain is checked on reads and
    hourly (purge_expired), so a write costs the same however large the store grows."""
    row = db.execute(
        "SELECT position,metadata,hash,payload IS NULL,expires FROM events "
        "ORDER BY position DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return 0, "0" * 64
    position, text, digest, empty, expires = row
    meta = json.loads(text)
    _check(meta, digest, position, meta["previous_hash"], empty, expires)
    return position + 1, digest


def record(event: str, payload: dict, **links) -> str:
    """Persist before returning success; no credentials in metadata or encrypted evidence."""
    try:
        cipher = _cipher()
        body = encoded(clean(payload))
        if len(body) > config.EVIDENCE_MAX_ITEM_BYTES:
            # Keep proof of what arrived without letting one request store megabytes.
            body = encoded({"oversized": True, "bytes": len(body), "sha256": sha(body)})
        binding = {**(context.get() or {}), **links}
        # Store caller-provided identifiers only inside encrypted evidence if malformed.
        binding = {
            k: v
            for k, v in binding.items()
            if k in {"request_id", "session_id", "draft_id", "revision", "draft_digest"}
            and isinstance(v, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v)
        }
        if "draft_digest" in binding:
            binding["draft_digest"] = _mac("draft-digest", binding["draft_digest"].encode())
        identifier = "ev_" + uuid.uuid4().hex
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            position, previous = _last(db)
            now = time.time()
            expires = now + max(1, config.AUDIT_RETENTION_DAYS) * 86400
            (size,) = db.execute(
                "SELECT page_count*page_size FROM pragma_page_count(), pragma_page_size()"
            ).fetchone()
            full = size > config.EVIDENCE_MAX_STORE_BYTES
            meta = {
                "position": position,
                "event_id": identifier,
                "event": event,
                "time": now,
                "expires_at": expires,
                **binding,
                "evidence_digest": _mac("evidence-digest", body),
                "previous_hash": previous,
                # ponytail: past the cap only metadata is kept; raise the cap or ship evidence
                # to object storage if a real deployment needs every payload.
                **({"dropped": "capacity"} if full else {}),
            }
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (
                    position,
                    encoded(meta).decode(),
                    _link(meta),
                    None if full else cipher.encrypt(body),
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
            for meta, digest, retained, _ in selected[-limit:]:
                state = "retained" if retained else meta.get("dropped") or "expired"
                item = {**meta, "hash": digest, "evidence_state": state}
                if retained and include_evidence:
                    (blob,) = db.execute(
                        "SELECT payload FROM events WHERE position=?", (meta["position"],)
                    ).fetchone()
                    body = _cipher().decrypt(blob)
                    if not hmac.compare_digest(
                        _mac("evidence-digest", body), meta["evidence_digest"]
                    ):
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
