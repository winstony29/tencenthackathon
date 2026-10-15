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
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app import config
from app.audit import AuditError

context: ContextVar[dict | None] = ContextVar("audit_context", default=None)
_SECRET = re.compile(
    r"authorization|cookie|password|secret|api.?key|private.?key|otp|receipt|token|credential"
    r"|cvv|card.?number|nric|signature|nonce|^pin$|^pass$|^pwd$|^key$"
    r"|^d$|^p$|^q$|^dp$|^dq$|^qi$",
    re.I,
)
# Secrets people type or say, replaced wherever text goes: Jev, the validator and evidence
# (M11, 15 Oct audit). Amounts and names are masked separately (app/intent/options.py).
_SAID = [
    (
        re.compile(
            r"\b(password|passcode|passwd|pwd|pin|otp|one[- ]time (?:password|code|pin)"
            r"|verification code|security code|cvv|cvc)(\s*(?:is|was|=|:)?\s*)\S+",
            re.I,
        ),
        r"\1\2[secret]",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|eyJ[\w-]+\.[\w-]+\.[\w-]+)"
        ),
        "[secret]",
    ),
    (re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.I), "[id]"),  # Singapore NRIC / FIN
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
]
_CARD = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn(digits: str) -> bool:
    total = sum(
        int(d) if i % 2 == 0 else sum(divmod(int(d) * 2, 10)) for i, d in enumerate(digits[::-1])
    )
    return total % 10 == 0


def scrub(text: str) -> str:
    """Replace typed credentials, card numbers, NRICs and API keys with placeholders."""
    for pattern, replacement in _SAID:
        text = pattern.sub(replacement, text)
    return _CARD.sub(
        lambda m: "[card]" if _luhn(re.sub(r"\D", "", m.group(0))) else m.group(0), text
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
                value = re.sub(re.escape(secret), "[REDACTED]", value, flags=re.I)
        return scrub(value)
    return value


def encoded(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _keys() -> list[str]:
    """AUDIT_EVIDENCE_KEY may list keys, newest first: "new,old". The newest encrypts and
    signs; older ones still verify and decrypt, so a rotation is no outage (M15). A retired
    key must stay listed while rows it signed remain. ponytail: no crypto-shredding of
    expired payloads in backups; add per-period keys if that matters."""
    return [k.strip() for k in config.AUDIT_EVIDENCE_KEY.split(",") if k.strip()]


def _mac(label: str, value: bytes, key: str) -> str:
    """Keyed digest: a plain SHA-256 of a short request in plaintext metadata lets anyone
    holding the file confirm guesses such as "send 350 to John Doe" (H6, 15 Oct audit)."""
    derived = hmac.new(key.encode(), label.encode(), hashlib.sha256).digest()
    return hmac.new(derived, value, hashlib.sha256).hexdigest()


def _signed(label: str, value: bytes, digest: str) -> bool:
    return any(hmac.compare_digest(_mac(label, value, k), digest) for k in _keys())


def _link(meta: dict, key: str) -> str:
    """Keyed chain hash: rewriting history needs the evidence key, not just file access."""
    derived = hashlib.sha256(b"dcta-evidence-chain:" + key.encode()).digest()
    return hmac.new(derived, encoded(meta), hashlib.sha256).hexdigest()


def _cipher() -> MultiFernet:
    try:
        return MultiFernet([Fernet(k.encode()) for k in _keys()])
    except (ValueError, TypeError):
        raise AuditError("Audit evidence encryption is not configured.") from None


class _Tampered(AuditError):
    pass


# Per store: the next position and head hash this process wrote or verified, and stores
# found tampered. Rows deleted from the end, or all of them, leave a chain that verifies;
# the remembered head catches that (M8). ponytail: in memory, so a restart trusts the file
# again; keep a keyed checkpoint outside the database if that gap matters.
_heads: dict[str, tuple[int, str]] = {}
_broken: set[str] = set()
_lock = threading.Lock()  # a commit and its head update happen together


def _path() -> Path:
    if config.AUDIT_EVIDENCE_PATH:
        return Path(config.AUDIT_EVIDENCE_PATH)
    return Path(__file__).resolve().parents[1] / "var" / "evidence.sqlite3"


@contextmanager
def _connect():
    path = _path()
    if not _keys():  # without a key nothing can be checked; don't mistake that for tampering
        raise AuditError("Audit evidence encryption is not configured.")
    if str(path) in _broken:  # fail closed: never append to a store found tampered
        raise _Tampered("Evidence timeline integrity check failed earlier.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Transcripts and bank snapshots, even encrypted, are owner-only: create the file 0600
    # before SQLite can create it under the umask (L15), and tighten an older file.
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
    os.chmod(path, 0o600)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA secure_delete=ON")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        position INTEGER PRIMARY KEY, metadata TEXT NOT NULL, hash TEXT NOT NULL,
        payload BLOB, expires REAL NOT NULL)""")
    try:
        with db:
            yield db
    except _Tampered:
        _broken.add(str(path))
        raise
    finally:
        db.close()


def _check(meta: dict, digest: str, position: int, previous: str, empty: bool, expires: float):
    if (
        meta["position"] != position
        or meta["expires_at"] != expires
        or meta["previous_hash"] != previous
        # Evidence may be absent only after expiry, or when capacity dropped it on write.
        or (empty and expires > time.time() and not meta.get("dropped"))
        or not any(hmac.compare_digest(_link(meta, k), digest) for k in _keys())
    ):
        raise _Tampered("Evidence timeline integrity check failed.")


def _head(position: int, digest: str) -> None:
    """Refuse a chain that ends before what this process last saw; then remember its end."""
    seen = _heads.get(str(_path()))
    if seen is not None and seen != (position, digest):
        raise _Tampered("Evidence timeline was truncated or rolled back.")
    _heads[str(_path())] = (position, digest)


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
            raise _Tampered("Evidence timeline integrity check failed.")
        _check(meta, digest, position, previous, empty, expires)
        previous = digest
        result.append((meta, digest, not empty, expires))
    _head(len(result), previous)
    return result


def _last(db) -> tuple[int, str]:
    """Check only the newest link before appending; the full chain is checked on reads and
    hourly (purge_expired), so a write costs the same however large the store grows."""
    row = db.execute(
        "SELECT position,metadata,hash,payload IS NULL,expires FROM events "
        "ORDER BY position DESC LIMIT 1"
    ).fetchone()
    if row is None:
        _head(0, "0" * 64)
        return 0, "0" * 64
    position, text, digest, empty, expires = row
    meta = json.loads(text)
    _check(meta, digest, position, meta["previous_hash"], empty, expires)
    _head(position + 1, digest)
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
        key = _keys()[0]  # _cipher() has checked there is one
        if "draft_digest" in binding:
            binding["draft_digest"] = _mac("draft-digest", binding["draft_digest"].encode(), key)
        identifier = "ev_" + uuid.uuid4().hex
        with _lock, _connect() as db:
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
                "evidence_digest": _mac("evidence-digest", body, key),
                "previous_hash": previous,
                # ponytail: past the cap only metadata is kept; raise the cap or ship evidence
                # to object storage if a real deployment needs every payload.
                **({"dropped": "capacity"} if full else {}),
            }
            link = _link(meta, key)
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (
                    position,
                    encoded(meta).decode(),
                    link,
                    None if full else cipher.encrypt(body),
                    expires,
                ),
            )
            db.commit()
            _heads[str(_path())] = (position + 1, link)  # only once committed
        return identifier
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise AuditError("Could not persist audit evidence.") from error


def timeline(session_id: str | None, limit: int, include_evidence: bool) -> dict:
    """Verify the chain and payload digests; expiry never silently becomes an empty record."""
    try:
        with _lock, _connect() as db:
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
                    if not _signed("evidence-digest", body, meta["evidence_digest"]):
                        raise AuditError("Evidence digest mismatch.")
                    item["evidence"] = json.loads(body)
                entries.append(item)
            return {"chain_valid": True, "entries": entries}
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, InvalidToken) as error:
        raise AuditError("Audit evidence is unavailable or corrupted.") from error


def purge_expired() -> None:
    """Run on startup and hourly; retain integrity metadata after evidence expiry."""
    try:
        with _lock, _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            _verified(db)
            db.execute("UPDATE events SET payload=NULL WHERE expires<=?", (time.time(),))
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise AuditError("Audit retention maintenance failed.") from error
