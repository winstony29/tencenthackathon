"""Audit log: append-only, hash-chained record of every important event.

Owner: Jayanth
Diagram box: Audit log
Contract: docs/CONTRACTS.md §3.3 (GET /api/audit); anyone may call it, only Jayanth changes it
"""

import hashlib
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from pydantic import ValidationError

from app.contracts import AuditEntry, AuditResponse


class AuditError(RuntimeError):
    """Audit storage is unsafe or unavailable; mandatory callers must stop processing."""


# A chain cannot detect valid tail truncation or full rewriting and rehashing.
# Independently protected checkpoints would be needed to detect those attacks.
# Native file locks serialize local processes; this is not distributed storage.
_THREAD_LOCK = RLock()


def _canonical(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in record.items() if k != "hash"})).hexdigest()


def _safe_data(data: dict[str, Any]) -> dict[str, Any]:
    # A flat allowlist prevents transcripts and credentials hiding in nested metadata.
    clean = {}
    for key, value in data.items():
        if key in {"user_id", "draft_id", "session_id", "transaction_id", "account_id"}:
            valid = isinstance(value, str) and re.fullmatch(r"[a-z]+_[A-Za-z0-9_-]{1,80}", value)
        elif key in {"reason", "field", "ticker", "currency"}:
            valid = isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,80}", value)
        elif key == "amount":
            valid = isinstance(value, str) and re.fullmatch(r"\d{1,18}\.\d{2}", value)
        elif key in {"action_index", "count"}:
            valid = type(value) is int and value >= 0
        else:
            valid = False
        if not valid:
            raise AuditError("Audit metadata rejected; use approved non-sensitive fields")
        clean[key] = value
    return clean


@contextmanager
def _locked_file(storage_path: Path | None) -> Iterator[BinaryIO]:
    path = storage_path or Path(__file__).resolve().parents[1] / "var" / "audit.jsonl"
    try:
        with _THREAD_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.with_suffix(".lock").open("a+b") as lock:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    with path.open("a+b") as stream:
                        yield stream
                finally:
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        raise AuditError("Audit storage operation failed") from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditError("Audit record contains duplicate fields")
        result[key] = value
    return result


def _read_records(stream: BinaryIO) -> list[dict[str, Any]]:
    stream.seek(0)
    records = []
    for line in stream:
        if not line.endswith(b"\n"):
            raise AuditError("Audit log contains an incomplete record")
        try:
            record = json.loads(line, object_pairs_hook=_unique_object)
            AuditEntry.model_validate(record)
            if type(record["index"]) is not int or record["index"] < 0:
                raise ValueError
            if not all(
                isinstance(record[key], str)
                for key in ("time", "actor", "event", "prev_hash", "hash")
            ):
                raise ValueError
            _safe_data(record["data"])
            records.append(record)
        except (ValueError, TypeError, KeyError, ValidationError):
            raise AuditError("Audit log contains a malformed record") from None
    return records


def _chain_valid(records: list[dict[str, Any]]) -> bool:
    previous = "0" * 64
    for index, record in enumerate(records):
        if record["index"] != index or record["prev_hash"] != previous:
            return False
        if record["hash"] != _digest(record):
            return False
        previous = record["hash"]
    return True


def append_event(
    actor: str, event: str, data: dict[str, Any], *, storage_path: Path | None = None
) -> AuditEntry:
    """Persist one real event and return it; refuse unsafe data or a broken existing chain.

    Callers must propagate AuditError when recording is mandatory. Logging does not
    authorise or execute a transaction. No incidents are generated automatically.
    """
    if not all(
        isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", v) for v in (actor, event)
    ):
        raise AuditError("Audit actor or event code is invalid")
    clean = _safe_data(data)
    with _locked_file(storage_path) as stream:
        records = _read_records(stream)
        if not _chain_valid(records):
            raise AuditError("Audit chain verification failed; append refused")
        record = dict(
            index=len(records),
            time=datetime.now(UTC).isoformat(),
            actor=actor,
            event=event,
            data=clean,
            prev_hash=records[-1]["hash"] if records else "0" * 64,
        )
        record["hash"] = _digest(record)
        payload = _canonical(record) + b"\n"
        if stream.write(payload) != len(payload):
            raise AuditError("Audit record write was incomplete")
        stream.flush()
        os.fsync(stream.fileno())
        # Persist the directory entry too when this is the first append to a new file.
        # Directory fsync is POSIX-only; Windows still flushes the file above.
        if os.name != "nt":
            descriptor = os.open(str(Path(stream.name).parent), os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    return AuditEntry.model_validate(record)


def verify(*, storage_path: Path | None = None) -> bool:
    """Verify the complete chain; structural or I/O failures raise a safe AuditError."""
    with _locked_file(storage_path) as stream:
        return _chain_valid(_read_records(stream))


def read_events(limit: int, *, storage_path: Path | None = None) -> AuditResponse:
    """Return latest records oldest first, verifying the entire locked snapshot.

    Hash mismatches return chain_valid=False; unreadable or malformed logs raise
    AuditError rather than pretending that an empty log was successfully read.
    """
    if type(limit) is not int or limit <= 0:
        raise ValueError("Audit limit must be a positive integer")
    with _locked_file(storage_path) as stream:
        records = _read_records(stream)
        return AuditResponse(chain_valid=_chain_valid(records), entries=records[-limit:])
