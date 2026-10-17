"""Mock gateway: verifies validation receipts, challenges and customer signatures.

Owner: Jayanth
Diagram box: Mock gateway
Contract: docs/CONTRACTS.md §3.3 (/api/challenge, /api/execute) and §4

The integrated context provider uses supervised, encrypted approval state. The trusted
context lease must authenticate the requesting customer (or supervise demo access),
identify the latest independently validated revision and prevent its replacement
until execution finishes. Unconfigured execution fails closed. It never imports AI.
J-04 first enrollment remains unauthenticated: use a supervised private demo only.

Receipt v1: vr_1.<base64url canonical claims>.<base64url HMAC-SHA256>. Claims bind
all canonical Draft fields by SHA-256, user/session/draft, authoritative revision,
context and logical request, and issued/expiry times. Domain prefixes separate
receipt and storage MACs. Execution identity uses a domain-separated hash that
stays stable across signing-secret changes. The independent validation integration issues receipts.

Lock order: gateway -> context lease -> device/ledger -> audit. Never acquire the
gateway lock from those modules. Ledger and gateway are separate atomic stores:
persist inflight before calling J-08; recover with the SAME stable execution ID.
Committed exact retries return checked original evidence, never a second payment.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from fastapi import HTTPException

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app import audit, config, evidence
from app.bank import devices, ledger, readonly
from app.contracts import (
    ChallengeRequest,
    ChallengeResponse,
    Draft,
    ExecutedResponse,
    ExecuteRequest,
    ExecuteResponse,
    NeedsExtraCheckResponse,
    RejectedResponse,
)

_LOCK = threading.RLock()


class GatewayUnavailable(RuntimeError):
    """Fail closed; reason is controlled, never a storage path or customer input."""


class GatewayRejected(ValueError):
    """A controlled receipt/challenge/signature rejection; no new financial commit."""


class GatewayPolicy(BaseModel):
    """Required backend resource limits; tests supply fixtures, live defaults are absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    nonce_lifetime_seconds: int = Field(strict=True, gt=0)
    receipt_lifetime_seconds: int = Field(strict=True, gt=0)
    max_receipt_bytes: int = Field(strict=True, gt=0)
    max_draft_bytes: int = Field(strict=True, gt=0)
    max_store_bytes: int = Field(strict=True, gt=0)
    max_challenges: int = Field(strict=True, gt=0)
    max_challenges_per_user: int = Field(strict=True, gt=0)
    max_events: int = Field(strict=True, gt=0)
    max_attempts: int = Field(strict=True, gt=0)


class TrustedContext(BaseModel):
    """Backend-owned approval context, supplied only by a coordinated trusted lease.

    These identifiers must never be copied from a client as authority. logical_id
    identifies a customer's new request, stays stable across its revisions and is
    different for a genuinely new request with identical amounts/recipients.
    The provider must retain authoritative instructions, clarification history and
    derivation inputs. This model does not manufacture those records or authenticate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    draft_id: str = Field(min_length=1, max_length=80)
    context_id: str = Field(min_length=1, max_length=80)
    revision: str = Field(min_length=1, max_length=80)
    logical_id: str = Field(min_length=1, max_length=80)
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: AwareDatetime


class ReceiptClaims(BaseModel):
    """Strict opaque receipt envelope; no transcript, money, proof boolean or key."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    audience: Literal["dcta.transaction.validation"]
    user_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    draft_id: str = Field(min_length=1, max_length=80)
    context_id: str = Field(min_length=1, max_length=80)
    revision: str = Field(min_length=1, max_length=80)
    logical_id: str = Field(min_length=1, max_length=80)
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: AwareDatetime
    expires_at: AwareDatetime


class StepUpBinding(BaseModel):
    """Binding a future verifier must authenticate, consume once and expire.

    This is a requirement descriptor, NOT a proof or verified boolean. No verifier
    or proof-acceptance path exists yet. A later approved service must authenticate
    this complete binding, bound attempts per logical request and persist consumption;
    J-08 must still enforce hard limits and pause under its lock after verification.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: str
    execution_id: str
    nonce: str
    draft_hash: str
    context_id: str
    revision: str
    expires_at: AwareDatetime


def gateway_policy() -> GatewayPolicy | None:
    """Explicit opt-in backend limits; never accept policy from requests."""
    if not config.BANK_EXECUTION_ENABLED or not config.BANK_GATEWAY_POLICY:
        return None
    try:
        return GatewayPolicy.model_validate_json(config.BANK_GATEWAY_POLICY)
    except ValueError:
        return None


@contextmanager
def trusted_context(draft: Draft) -> Iterator[TrustedContext]:
    from app import transaction_state

    try:
        with transaction_state.execution_context(draft) as value:
            token = evidence.context.set(
                {**(evidence.context.get() or {}), "revision": value["revision"]}
            )
            try:
                yield TrustedContext.model_validate(value)
            finally:
                evidence.context.reset(token)
    except transaction_state.InvalidDraft:
        raise GatewayRejected("current_context_unavailable") from None


def _policy() -> GatewayPolicy:
    try:
        value = gateway_policy()
        if value is None:
            raise ValueError
        policy = GatewayPolicy.model_validate(value.model_dump(mode="python", warnings=False))
        # CONTRACTS §3.3 bounds approval nonces to five minutes; no live default.
        if timedelta(seconds=policy.nonce_lifetime_seconds) > timedelta(minutes=5):
            raise ValueError
        if policy.max_challenges_per_user > policy.max_challenges:
            raise ValueError
        return policy
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise GatewayUnavailable("gateway_policy_unavailable") from None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def canonical_draft(draft: Draft) -> str:
    """Canonical complete public draft, identical to J-08's fingerprint representation."""
    validated = Draft.model_validate(draft.model_dump(mode="python", warnings=False))
    return _canonical(validated.model_dump(mode="json")).decode("utf-8")


def _draft_hash(draft: Draft) -> str:
    return hashlib.sha256(canonical_draft(draft).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if _b64(raw) != value:
        raise ValueError
    return raw


def _secret() -> bytes:
    # Config owns environment access. No random fallback: receipts survive restart.
    try:
        key = _decode(config.VALIDATION_RECEIPT_KEY)
        if len(key) < hashlib.sha256().digest_size:
            raise ValueError
        return key
    except (ValueError, TypeError):
        raise GatewayUnavailable("receipt_key_unavailable") from None


def _mac(domain: bytes, payload: bytes) -> bytes:
    return hmac.digest(_secret(), domain + payload, "sha256")


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _authenticated_receipt(draft: Draft, receipt: str, policy: GatewayPolicy) -> ReceiptClaims:
    try:
        if not isinstance(receipt, str) or len(receipt.encode()) > policy.max_receipt_bytes:
            raise ValueError
        version, body, signature = receipt.split(".")
        if version != "vr_1":
            raise ValueError
        payload, supplied = _decode(body), _decode(signature)
        expected = _mac(b"DCTA-VALIDATION-RECEIPT\0v1\0", payload)
        if len(supplied) != hashlib.sha256().digest_size or not hmac.compare_digest(
            expected, supplied
        ):
            raise ValueError
        raw = json.loads(payload, object_pairs_hook=_unique)
        if not isinstance(raw, dict) or type(raw.get("version")) is not int:
            raise ValueError
        claims = ReceiptClaims.model_validate(raw)
        if _canonical(claims.model_dump(mode="json")) != payload:
            raise ValueError
        if (
            claims.draft_hash != _draft_hash(draft)
            or claims.user_id != draft.user_id
            or claims.session_id != draft.session_id
            or claims.draft_id != draft.draft_id
        ):
            raise ValueError
        lifetime = (claims.expires_at - claims.issued_at).total_seconds()
        if not 0 < lifetime <= policy.receipt_lifetime_seconds:
            raise ValueError
        return claims
    except (ValueError, TypeError, AttributeError, OverflowError, UnicodeError):
        raise GatewayRejected("invalid_receipt") from None


def _current(claims: ReceiptClaims, context: TrustedContext, now: datetime) -> None:
    try:
        context = TrustedContext.model_validate(context.model_dump(mode="python", warnings=False))
    except (ValueError, TypeError, AttributeError):
        raise GatewayRejected("invalid_receipt") from None
    if claims.issued_at > now or claims.expires_at <= now or context.expires_at <= now:
        raise GatewayRejected("receipt_expired")
    for field in (
        "user_id",
        "session_id",
        "draft_id",
        "context_id",
        "revision",
        "logical_id",
        "draft_hash",
    ):
        if getattr(claims, field) != getattr(context, field):
            raise GatewayRejected("receipt_superseded")
    if claims.expires_at > context.expires_at:
        raise GatewayRejected("invalid_receipt")


def verify_receipt(draft: Draft, receipt: str) -> ReceiptClaims:
    """Verify MAC, exact canonical binding, expiry and current authoritative context.

    A receipt is validation evidence, never customer authorization. There is no
    production issuer here. Missing source/authentication integration fails closed.
    """
    claims = _authenticated_receipt(draft, receipt, _policy())
    with trusted_context(draft) as context:
        _current(claims, context, _now())
    return claims


def _execution_id(claims: ReceiptClaims) -> str:
    return (
        "ex_"
        + hashlib.sha256(
            b"DCTA-EXECUTION-IDENTITY\0v1\0" + _canonical([claims.user_id, claims.logical_id])
        ).hexdigest()
    )


def _key(user_id: str) -> dict[str, str]:
    try:
        key = devices.get_public_key(user_id)
        if key is None:
            raise GatewayRejected("device_not_registered")
        return devices._normalize_public_key(key)
    except devices.DeviceStorageError:
        raise GatewayUnavailable("device_storage_unavailable") from None
    except HTTPException as error:
        if error.status_code >= 500:
            raise GatewayUnavailable("device_storage_unavailable") from None
        raise GatewayRejected("device_not_registered") from None


def _key_hash(key: dict[str, str]) -> str:
    return hashlib.sha256(_canonical(key)).hexdigest()


def verify_signature(key: dict[str, str], payload: str, signature: str) -> None:
    """Verify WebCrypto's strict base64url 64-byte r||s format using P-256/SHA-256."""
    try:
        normalized = devices._normalize_public_key(key)
        if not isinstance(signature, str) or len(signature) != 86:
            raise ValueError
        raw = _decode(signature)
        if len(raw) != 2 * hashlib.sha256().digest_size:
            raise ValueError
        public = ec.EllipticCurvePublicNumbers(
            int.from_bytes(_decode(normalized["x"]), "big"),
            int.from_bytes(_decode(normalized["y"]), "big"),
            ec.SECP256R1(),
        ).public_key()
        encoded = utils.encode_dss_signature(
            int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
        )
        public.verify(encoded, payload.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    except (ValueError, TypeError, InvalidSignature, HTTPException):
        raise GatewayRejected("bad_signature") from None


def _path(storage_path: Path | None) -> Path:
    return storage_path or Path(__file__).resolve().parents[2] / "var" / "nonces.json"


def _sync_directory(path: Path) -> None:
    # Windows flushes file contents; POSIX additionally syncs directory entries.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _locked(path: Path) -> Iterator[bool]:
    with _LOCK:
        try:
            existed = path.parent.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not existed:
                _sync_directory(path.parent.parent)
            with path.with_suffix(".lock").open("a+b") as lock:
                if os.name == "nt":
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    lock.seek(0)
                    established = bool(lock.read())
                    if not established:
                        lock.write(b"gateway-initialisation-started\n")
                        lock.flush()
                        os.fsync(lock.fileno())
                        _sync_directory(path.parent)
                    yield established
                finally:
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        except OSError:
            raise GatewayUnavailable("challenge_storage_unavailable") from None


def _save(path: Path, state: dict[str, Any], policy: GatewayPolicy) -> None:
    temporary = None
    try:
        body = {key: value for key, value in state.items() if key != "mac"}
        state["mac"] = _mac(b"DCTA-CHALLENGE-STORE\0v1\0", _canonical(body)).hex()
        payload = _canonical(state) + b"\n"
        if len(payload) > policy.max_store_bytes:
            raise GatewayUnavailable("challenge_capacity_exceeded")
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=".nonces-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except OSError:
        raise GatewayUnavailable("challenge_storage_unavailable") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read(path: Path, established: bool, policy: GatewayPolicy) -> dict[str, Any]:
    try:
        if not path.exists() and not established:
            return {"version": 1, "challenges": {}, "events": {}, "outbox": []}
        with path.open("rb") as stream:
            payload = stream.read(policy.max_store_bytes + 1)
        if len(payload) > policy.max_store_bytes or not payload.endswith(b"\n"):
            raise ValueError
        state = json.loads(payload, object_pairs_hook=_unique)
        if (
            set(state) != {"version", "challenges", "events", "outbox", "mac"}
            or type(state["version"]) is not int
            or state["version"] != 1
        ):
            raise ValueError
        body = {key: value for key, value in state.items() if key != "mac"}
        if not isinstance(state["mac"], str) or not hmac.compare_digest(
            state["mac"], _mac(b"DCTA-CHALLENGE-STORE\0v1\0", _canonical(body)).hex()
        ):
            raise ValueError
        if (
            not isinstance(state["challenges"], dict)
            or len(state["challenges"]) > policy.max_challenges
        ):
            raise ValueError
        for nonce, record in state["challenges"].items():
            _validate_record(nonce, record, policy)
        if not isinstance(state["events"], dict) or len(state["events"]) > policy.max_events:
            raise ValueError
        for identity, event in state["events"].items():
            if not re.fullmatch(r"[0-9a-f]{64}", identity) or set(event) != {
                "event",
                "data",
                "until",
            }:
                raise ValueError
            audit._safe_data(event["data"])
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event["event"]):
                raise ValueError
            readonly._forecast_time(event["until"])
        if (
            not isinstance(state["outbox"], list)
            or len(set(state["outbox"])) != len(state["outbox"])
            or any(i not in state["events"] for i in state["outbox"])
        ):
            raise ValueError
        return state
    except (ValueError, TypeError, KeyError, AttributeError, OSError, audit.AuditError):
        raise GatewayUnavailable("challenge_storage_unavailable") from None


def _validate_record(nonce: str, record: dict[str, Any], policy: GatewayPolicy) -> None:
    if not re.fullmatch(r"n_[A-Za-z0-9_-]{43}", nonce) or len(_decode(nonce[2:])) != 32:
        raise ValueError
    if set(record) - {
        "require_otp",
        "require_fingerprint",
    } != {  # require_otp is absent on records before D-054
        "draft",
        "receipt",
        "execution_id",
        "key_hash",
        "created_at",
        "expires_at",
        "status",
        "signature_hash",
        "attempts",
        "step_up_attempts",
        "result",
    }:
        raise ValueError
    draft = Draft.model_validate(record["draft"])
    if len(canonical_draft(draft).encode()) > policy.max_draft_bytes:
        raise ValueError
    claims = _authenticated_receipt(draft, record["receipt"], policy)
    if record["execution_id"] != _execution_id(claims) or not re.fullmatch(
        r"[0-9a-f]{64}", record["key_hash"]
    ):
        raise ValueError
    created = readonly._forecast_time(record["created_at"])
    expires = readonly._forecast_time(record["expires_at"])
    if (
        not created < expires <= claims.expires_at
        or (expires - created).total_seconds() > policy.nonce_lifetime_seconds
    ):
        raise ValueError
    if record["status"] not in {
        "issued",
        "inflight",
        "committed",
        "rejected",
        "step_up",
        "invalidated",
        "locked",
    }:
        raise ValueError
    if record["signature_hash"] is not None and not re.fullmatch(
        r"[0-9a-f]{64}", record["signature_hash"]
    ):
        raise ValueError
    if (
        record["status"] in {"inflight", "committed", "step_up", "rejected"}
        and record["signature_hash"] is None
    ):
        raise ValueError
    if any(
        type(record[f]) is not int or not 0 <= record[f] <= policy.max_attempts
        for f in ("attempts", "step_up_attempts")
    ):
        raise ValueError
    if any(
        type(record.get(key, False)) is not bool for key in ("require_otp", "require_fingerprint")
    ):  # absent on records before D-054
        raise ValueError
    if record["status"] == "committed":
        ExecutedResponse.model_validate(record["result"])
    elif record["result"] is not None:
        raise ValueError


def _event(
    event: str, reason: str, identity: str, execution_id: str, until: datetime
) -> dict[str, Any]:
    # Hash correlation: no raw transcript, customer ID, account, money, nonce, OTP or MAC.
    return {
        "event": event,
        "data": {
            "transaction_id": "tx_" + hashlib.sha256(identity.encode()).hexdigest(),
            "draft_id": "d_" + hashlib.sha256(execution_id.encode()).hexdigest(),
            "reason": reason,
        },
        "until": until.isoformat(),
    }


def _queue(state: dict[str, Any], event: dict[str, Any], path: Path, policy: GatewayPolicy) -> None:
    identity = hashlib.sha256(_canonical({k: event[k] for k in ("event", "data")})).hexdigest()
    if identity not in state["events"]:
        if len(state["events"]) >= policy.max_events:
            raise GatewayUnavailable("challenge_capacity_exceeded")
        state["events"][identity] = event
        state["outbox"].append(identity)
    _save(path, state, policy)


def _reconcile(
    state: dict[str, Any], path: Path, policy: GatewayPolicy, audit_path: Path | None
) -> None:
    try:
        log = audit.read_events(sys.maxsize, storage_path=audit_path)
        if not log.chain_valid:
            raise audit.AuditError("Invalid audit chain")
        delivered = {
            _canonical({"event": e.event, "data": e.data}) for e in log.entries if e.actor == "bank"
        }
        expected = {
            _canonical({k: event[k] for k in ("event", "data")})
            for event in state["events"].values()
        }
        pending = {
            _canonical({k: state["events"][i][k] for k in ("event", "data")})
            for i in state["outbox"]
        }
        if not expected.issubset(delivered | pending):
            raise audit.AuditError("Missing gateway evidence")
        for identity in state["outbox"]:
            event = state["events"][identity]
            audit.append_event(
                "bank", event["event"], event["data"], storage_path=audit_path, idempotent=True
            )
        if state["outbox"]:
            state["outbox"] = []
            _save(path, state, policy)
    except audit.AuditError:
        raise GatewayUnavailable("audit_reconciliation_required") from None


def _cleanup(state: dict[str, Any], now: datetime, policy: GatewayPolicy) -> None:
    for nonce, record in list(state["challenges"].items()):
        claims = ReceiptClaims.model_validate(json.loads(_decode(record["receipt"].split(".")[1])))
        # Inflight records may describe a debit whose response/ack was lost: retain
        # until explicitly recovered, even if capacity must fail closed meanwhile.
        if record["status"] != "inflight" and claims.expires_at <= now:
            del state["challenges"][nonce]
    for identity, event in list(state["events"].items()):
        if identity not in state["outbox"] and readonly._forecast_time(event["until"]) <= now:
            del state["events"][identity]


def _audit_early(reason: str, identity: str, audit_path: Path | None) -> None:
    event = _event("validation_receipt_invalid", reason, identity, identity, _now())
    try:
        audit.append_event(
            "bank", event["event"], event["data"], storage_path=audit_path, idempotent=True
        )
    except audit.AuditError:
        raise GatewayUnavailable("audit_reconciliation_required") from None


def _ledger_state(ledger_path: Path | None) -> dict[str, Any]:
    try:
        # Never call a writer to discover whether banking state is initialised.
        return readonly._read_ledger_state(readonly._ledger_path(ledger_path))
    except readonly.LedgerStorageError:
        raise GatewayUnavailable("ledger_storage_unavailable") from None


def _audit_unknown_nonce(nonce: str, audit_path: Path | None) -> None:
    identity = hashlib.sha256(nonce.encode()).hexdigest()
    event = _event("nonce_replay_detected", "unknown_nonce", identity, identity, _now())
    try:
        audit.append_event(
            "bank", event["event"], event["data"], storage_path=audit_path, idempotent=True
        )
    except audit.AuditError:
        raise GatewayUnavailable("audit_reconciliation_required") from None


def _establish(
    state: dict[str, Any], path: Path, policy: GatewayPolicy, audit_path: Path | None
) -> None:
    if path.exists():
        return
    try:
        log = audit.read_events(sys.maxsize, storage_path=audit_path)
        if not log.chain_valid or any(e.event.startswith("gateway_") for e in log.entries):
            raise GatewayUnavailable("challenge_storage_unavailable")
        _save(path, state, policy)
    except audit.AuditError:
        raise GatewayUnavailable("audit_reconciliation_required") from None


def _commit(
    draft: Draft,
    execution_id: str,
    ledger_path: Path | None,
    audit_path: Path | None,
    *,
    step_up_satisfied: str | None = None,
) -> ExecutedResponse:
    try:
        return ledger.execute_batch(
            draft,
            execution_id,
            storage_path=ledger_path,
            audit_path=audit_path,
            step_up_satisfied=step_up_satisfied,
        )
    except (readonly.LedgerStorageError, ledger.LedgerAuditPending):
        raise GatewayUnavailable("execution_recovery_required") from None


def issue_challenge(
    request: ChallengeRequest,
    *,
    storage_path: Path | None = None,
    ledger_path: Path | None = None,
    audit_path: Path | None = None,
) -> ChallengeResponse:
    """Issue a persisted nonce only for an authenticated, current validation context."""
    policy = _policy()
    request = ChallengeRequest.model_validate(request.model_dump(mode="python", warnings=False))
    draft = request.draft
    if len(canonical_draft(draft).encode()) > policy.max_draft_bytes:
        raise GatewayRejected("draft_too_large")
    try:
        claims = _authenticated_receipt(draft, request.receipt, policy)
    except GatewayRejected:
        _audit_early("invalid_receipt", _draft_hash(draft), audit_path)
        raise
    path = _path(storage_path)
    with _locked(path) as established:
        state = _read(path, established, policy)
        _establish(state, path, policy, audit_path)
        _reconcile(state, path, policy, audit_path)
        now = _now()
        _cleanup(state, now, policy)
        with trusted_context(draft) as context:
            try:
                now = _now()
                _current(claims, context, now)
            except GatewayRejected as error:
                _audit_early(str(error), _draft_hash(draft), audit_path)
                raise
            execution_id = _execution_id(claims)
            if (
                sum(
                    r["step_up_attempts"]
                    for r in state["challenges"].values()
                    if r["execution_id"] == execution_id
                )
                >= policy.max_attempts
            ):
                raise GatewayRejected("step_up_locked")
            if execution_id in _ledger_state(ledger_path)["executions"]:
                raise GatewayRejected("logical_transaction_committed")
            key = _key(draft.user_id)
            for record in state["challenges"].values():
                if record["execution_id"] != execution_id:
                    continue
                if record["status"] in {"inflight", "committed"}:
                    raise GatewayUnavailable("execution_recovery_required")
                if _draft_hash(Draft.model_validate(record["draft"])) != claims.draft_hash:
                    record["status"] = "invalidated"
            if (
                len(state["challenges"]) >= policy.max_challenges
                or sum(r["draft"]["user_id"] == draft.user_id for r in state["challenges"].values())
                >= policy.max_challenges_per_user
            ):
                raise GatewayUnavailable("challenge_capacity_exceeded")
            nonce = "n_" + secrets.token_urlsafe(32)
            if nonce in state["challenges"]:
                raise GatewayUnavailable("nonce_collision")
            expires = min(
                now + timedelta(seconds=policy.nonce_lifetime_seconds),
                claims.expires_at,
                context.expires_at,
            )
            state["challenges"][nonce] = {
                "draft": draft.model_dump(mode="json"),
                "receipt": request.receipt,
                "execution_id": execution_id,
                "key_hash": _key_hash(key),
                "created_at": now.isoformat(),
                "expires_at": expires.isoformat(),
                "status": "issued",
                "signature_hash": None,
                "attempts": 0,
                "step_up_attempts": 0,
                "result": None,
                "require_otp": request.require_otp,
                "require_fingerprint": request.require_fingerprint,
            }
            _queue(
                state,
                _event(
                    "gateway_challenge_issued",
                    "validated_context",
                    nonce,
                    execution_id,
                    claims.expires_at,
                ),
                path,
                policy,
            )
            _reconcile(state, path, policy, audit_path)
            return ChallengeResponse(
                nonce=nonce,
                signing_payload=canonical_draft(draft) + "." + nonce,
                expires_at=expires,
            )


def _record_event(
    state: dict[str, Any],
    record: dict[str, Any],
    nonce: str,
    event: str,
    reason: str,
    path: Path,
    policy: GatewayPolicy,
    audit_path: Path | None,
    label: str = "",
) -> None:
    _queue(
        state,
        _event(
            event,
            reason,
            nonce + event + reason + label,
            record["execution_id"],
            readonly._forecast_time(record["expires_at"]),
        ),
        path,
        policy,
    )
    _reconcile(state, path, policy, audit_path)


def _risk_response(error: ledger.LedgerRejected) -> ExecuteResponse:
    if isinstance(error, ledger.LedgerRiskRejected) and error.status == "needs_extra_check":
        method = error.method if hasattr(error, "method") else "otp"
        return NeedsExtraCheckResponse(
            status="needs_extra_check",
            method=method,
            message=(
                "Unusual transaction activity detected. Confirm with your fingerprint."
                if method == "fingerprint"
                else "Additional verification is required; verified step-up is not available."
            ),
        )
    reason = error.reason
    if reason in {"per_transfer_limit_exceeded", "daily_limit_exceeded"}:
        reason = "limit_exceeded"
    if reason in {"payments_paused", "policy_missing", "policy_invalid", "policy_unsupported"}:
        raise GatewayUnavailable(reason)
    if reason in {
        "limit_exceeded",
        "velocity_exceeded",
        "kyc_incomplete",
        "insufficient_funds",
        "rate_changed",
    }:
        return RejectedResponse(
            status="rejected", reason=reason, message="The bank rejected this transaction."
        )
    raise GatewayRejected("transaction_ineligible")


def execute(
    request: ExecuteRequest,
    *,
    storage_path: Path | None = None,
    ledger_path: Path | None = None,
    audit_path: Path | None = None,
) -> ExecuteResponse:
    """Verify and execute once, or recover the identical previously committed request.

    No OTP is accepted as proof; unsupported step-up remains uncommitted and has
    bounded attempts. An inflight storage/audit error has an uncertain outcome:
    retry the original nonce/signature. Expired exact retries may recover ONLY an
    actual committed J-08 record; they can never initiate a fresh financial commit.
    """
    policy = _policy()
    request = ExecuteRequest.model_validate(request.model_dump(mode="python", warnings=False))
    if not re.fullmatch(r"n_[A-Za-z0-9_-]{43}", request.nonce):
        _audit_unknown_nonce(request.nonce, audit_path)
        raise GatewayRejected("unknown_nonce")
    path = _path(storage_path)
    with _locked(path) as established:
        state = _read(path, established, policy)
        _establish(state, path, policy, audit_path)
        _reconcile(state, path, policy, audit_path)
        record = state["challenges"].get(request.nonce)
        if record is None:
            _audit_unknown_nonce(request.nonce, audit_path)
            raise GatewayRejected("unknown_nonce")
        draft = Draft.model_validate(record["draft"])
        claims = _authenticated_receipt(draft, record["receipt"], policy)
        key = _key(draft.user_id)
        payload = canonical_draft(draft) + "." + request.nonce
        try:
            if _key_hash(key) != record["key_hash"]:
                raise GatewayRejected("bad_signature")
            verify_signature(key, payload, request.signature)
        except GatewayRejected:
            record["attempts"] = min(record["attempts"] + 1, policy.max_attempts)
            if record["attempts"] >= policy.max_attempts and record["status"] in {
                "issued",
                "step_up",
            }:
                record["status"] = "locked"
            _record_event(
                state,
                record,
                request.nonce,
                "signature_invalid",
                "bad_signature",
                path,
                policy,
                audit_path,
                str(record["attempts"]),
            )
            raise
        signature_hash = hashlib.sha256(request.signature.encode()).hexdigest()
        committed = _ledger_state(ledger_path)["executions"].get(record["execution_id"])
        if committed is not None:
            if (
                record["status"] not in {"inflight", "committed"}
                or record["signature_hash"] != signature_hash
            ):
                _record_event(
                    state,
                    record,
                    request.nonce,
                    "nonce_replay_detected",
                    "nonce_used",
                    path,
                    policy,
                    audit_path,
                )
                raise GatewayRejected("nonce_used")
            if committed["fingerprint"] != _draft_hash(draft):
                raise GatewayUnavailable("execution_identity_conflict")
            result = _commit(draft, record["execution_id"], ledger_path, audit_path)
            if record["result"] is not None and record["result"] != result.model_dump(mode="json"):
                raise GatewayUnavailable("execution_identity_conflict")
            record["status"], record["result"] = "committed", result.model_dump(mode="json")
            _record_event(
                state,
                record,
                request.nonce,
                "gateway_execution_recovered",
                "already_committed",
                path,
                policy,
                audit_path,
            )
            return result
        if record["status"] == "committed":
            raise GatewayUnavailable("committed_execution_missing")
        if record["status"] in {"invalidated", "locked", "rejected"}:
            _record_event(
                state,
                record,
                request.nonce,
                "nonce_replay_detected",
                "nonce_used",
                path,
                policy,
                audit_path,
            )
            raise GatewayRejected("nonce_used")
        now = _now()
        if readonly._forecast_time(record["expires_at"]) <= now:
            record["status"] = "invalidated"
            _record_event(
                state,
                record,
                request.nonce,
                "nonce_expired",
                "nonce_expired",
                path,
                policy,
                audit_path,
            )
            raise GatewayRejected("nonce_expired")
        if record["signature_hash"] is not None and record["signature_hash"] != signature_hash:
            raise GatewayRejected("nonce_used")
        with trusted_context(draft) as context:
            try:
                now = _now()
                if readonly._forecast_time(record["expires_at"]) <= now:
                    raise GatewayRejected("nonce_expired")
                _current(claims, context, now)
            except GatewayRejected as error:
                record["status"] = "invalidated"
                _record_event(
                    state,
                    record,
                    request.nonce,
                    "validation_receipt_invalid",
                    str(error),
                    path,
                    policy,
                    audit_path,
                )
                raise
            record["status"], record["signature_hash"] = "inflight", signature_hash
            _record_event(
                state,
                record,
                request.nonce,
                "gateway_execution_started",
                "signature_verified",
                path,
                policy,
                audit_path,
            )
            if record.get("require_fingerprint") and request.fingerprint is not True:
                # shortcut: demo confirmation only, replace with WebAuthn for biometric proof.
                record["status"] = "step_up"
                _record_event(
                    state,
                    record,
                    request.nonce,
                    "gateway_step_up_required",
                    "simulated_fingerprint_required",
                    path,
                    policy,
                    audit_path,
                )
                return NeedsExtraCheckResponse(
                    status="needs_extra_check",
                    method="fingerprint",
                    message="Voice not confirmed. Confirm with your fingerprint to continue.",
                )
            if request.fingerprint is True:
                _record_event(
                    state,
                    record,
                    request.nonce,
                    "gateway_step_up_confirmed",
                    "simulated_fingerprint",
                    path,
                    policy,
                    audit_path,
                )
            if record.get("require_otp"):
                # D-054: the client asked for the one-time-code step (the voice check was
                # unsure). The demo code is verified here; the ledger's own risk step-up
                # below is unchanged and still never commits.
                good = request.otp is not None and hmac.compare_digest(
                    request.otp.encode(), config.DEMO_OTP.encode()
                )
                if not good:
                    record["status"] = "step_up"
                    if request.otp is not None:
                        record["step_up_attempts"] = min(
                            record["step_up_attempts"] + 1, policy.max_attempts
                        )
                        if record["step_up_attempts"] >= policy.max_attempts:
                            record["status"] = "locked"
                    _record_event(
                        state,
                        record,
                        request.nonce,
                        "gateway_step_up_required",
                        "otp_requested" if request.otp is None else "bad_otp",
                        path,
                        policy,
                        audit_path,
                        str(record["step_up_attempts"]),
                    )
                    if record["status"] == "locked":
                        return RejectedResponse(
                            status="rejected",
                            reason="bad_otp",
                            message="Too many wrong codes; start a new request.",
                        )
                    return NeedsExtraCheckResponse(
                        status="needs_extra_check",
                        method="otp",
                        message="Enter the one-time code to continue."
                        if request.otp is None
                        else "That code didn't match. Try again.",
                    )
            try:
                result = _commit(
                    draft,
                    record["execution_id"],
                    ledger_path,
                    audit_path,
                    step_up_satisfied="fingerprint" if request.fingerprint else None,
                )
            except ledger.LedgerRejected as error:
                record["status"] = (
                    "step_up"
                    if isinstance(error, ledger.LedgerRiskRejected)
                    and error.status == "needs_extra_check"
                    else "rejected"
                )
                # OTP and fingerprint step-up both count attempts under the same lock.
                if record["status"] == "step_up" and (
                    request.otp is not None or request.fingerprint is not None
                ):
                    record["step_up_attempts"] = min(
                        record["step_up_attempts"] + 1, policy.max_attempts
                    )
                    attempts = sum(
                        r["step_up_attempts"]
                        for r in state["challenges"].values()
                        if r["execution_id"] == record["execution_id"]
                    )
                    if attempts >= policy.max_attempts:
                        for candidate in state["challenges"].values():
                            if candidate["execution_id"] == record["execution_id"]:
                                candidate["status"] = "locked"
                _record_event(
                    state,
                    record,
                    request.nonce,
                    "gateway_transaction_rejected",
                    error.reason,
                    path,
                    policy,
                    audit_path,
                    str(record["step_up_attempts"]),
                )
                if record["status"] == "locked":
                    method = error.method if hasattr(error, "method") else "otp"
                    return RejectedResponse(
                        status="rejected",
                        reason="bad_fingerprint" if method == "fingerprint" else "bad_otp",
                        message="Additional verification is unavailable; start a new request.",
                    )
                return _risk_response(error)
            except (readonly.LedgerStorageError, ledger.LedgerAuditPending):
                raise GatewayUnavailable("execution_recovery_required") from None
            record["status"], record["result"] = "committed", result.model_dump(mode="json")
            _record_event(
                state,
                record,
                request.nonce,
                "gateway_execution_confirmed",
                "committed",
                path,
                policy,
                audit_path,
            )
            return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Supervised local bank operations")
    parser.add_argument(
        "operation", choices=["initialize", "reconcile", "pause", "resume", "sync-catalogue"]
    )
    parser.add_argument("--confirm-fresh-install", action="store_true")
    args = parser.parse_args()
    if not config.BANK_EXECUTION_ENABLED or not config.BANK_SUPERVISED_USER:
        parser.error("Configure bank execution and a supervised customer first.")
    _policy()
    _secret()
    if args.operation == "initialize":
        path = readonly._ledger_path(None)
        if not args.confirm_fresh_install or path.exists() or path.with_suffix(".lock").exists():
            parser.error("Initialization requires an explicitly confirmed fresh installation.")
        ledger.reconcile_audit()
        print("Bank initialized with payments paused.")
    elif args.operation == "sync-catalogue":
        print(f"Added {ledger.sync_demo_catalogue()} payees; existing financial state preserved.")
    elif args.operation == "reconcile":
        ledger.reconcile_audit()
        print("Ledger audit delivery reconciled; no new payment submitted.")
    else:
        ledger.set_payment_pause(
            args.operation == "pause", operation_id="op_" + secrets.token_hex(16)
        )
        print("Payment control updated.")
