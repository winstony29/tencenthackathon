"""Device keys: stores each user's registered public key.

Owner: Jayanth
Diagram box: Mock gateway
Contract: docs/CONTRACTS.md §3.3 (/api/device/register) and §4
"""

import base64
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from fastapi import HTTPException

from app.bank.readonly import get_accounts
from app.contracts import DeviceRegisterResponse


class DeviceStorageError(RuntimeError):
    """The device registry is unavailable or corrupt; never reset it automatically."""


_THREAD_LOCK = RLock()


def _invalid_key() -> HTTPException:
    return HTTPException(status_code=400, detail="Invalid ECDSA P-256 public key.")


def _coordinate(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise _invalid_key()
    raw = base64.urlsafe_b64decode(value + "=")
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise _invalid_key()
    return int.from_bytes(raw, "big")


def _normalize_public_key(jwk: dict[str, Any]) -> dict[str, str]:
    # Accept WebCrypto's public metadata; never persist arbitrary caller fields.
    allowed = {"kty", "crv", "x", "y", "ext", "key_ops", "alg", "use", "kid"}
    if not isinstance(jwk, dict) or set(jwk) - allowed:
        raise _invalid_key()
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise _invalid_key()
    if "ext" in jwk and type(jwk["ext"]) is not bool:
        raise _invalid_key()
    if "key_ops" in jwk and jwk["key_ops"] != ["verify"]:
        raise _invalid_key()
    if "alg" in jwk and jwk["alg"] != "ES256":
        raise _invalid_key()
    if "use" in jwk and jwk["use"] != "sig":
        raise _invalid_key()
    if "kid" in jwk and not isinstance(jwk["kid"], str):
        raise _invalid_key()
    x, y = _coordinate(jwk.get("x")), _coordinate(jwk.get("y"))
    # Fixed P-256 domain parameters, not runtime settings (RFC 5903 section 3.1).
    prime = int("ffffffff00000001000000000000000000000000ffffffffffffffffffffffff", 16)
    coefficient = int("5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b", 16)
    if x >= prime or y >= prime or (y * y - (x * x * x - 3 * x + coefficient)) % prime:
        raise _invalid_key()
    return {field: jwk[field] for field in ("kty", "crv", "x", "y")}


@contextmanager
def _locked_registry(storage_path: Path | None) -> Iterator[Path]:
    path = storage_path or Path(__file__).resolve().parents[2] / "var" / "devices.json"
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
                    yield path
                finally:
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError:
        raise DeviceStorageError("Device registry storage operation failed") from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeviceStorageError("Device registry contains duplicate fields")
        result[key] = value
    return result


def _read_registry(path: Path) -> dict[str, dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeError:
        raise DeviceStorageError("Device registry contains invalid data") from None
    try:
        registry = json.loads(text, object_pairs_hook=_unique_object)
        if not isinstance(registry, dict):
            raise ValueError
        for user_id, jwk in registry.items():
            if not re.fullmatch(r"u_[A-Za-z0-9_-]+", user_id):
                raise ValueError
            if _normalize_public_key(jwk) != jwk:
                raise ValueError
        return registry
    except (ValueError, TypeError, HTTPException):
        raise DeviceStorageError("Device registry contains invalid data") from None


def _write_registry(path: Path, registry: dict[str, dict[str, str]]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="devices-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(registry, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Directory fsync is POSIX-only; Windows still flushes the file above.
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def register_public_key(
    user_id: str, public_key_jwk: dict[str, Any], *, storage_path: Path | None = None
) -> DeviceRegisterResponse:
    """Pin a known demo user's first public key, accepting identical repeat registration.

    This checks user existence, NOT customer identity or private-key possession.
    First-registration takeover is possible: use supervised private demo enrollment
    only. Replacement/recovery needs authenticated customer and step-up checks later.
    """
    get_accounts(user_id)
    key = _normalize_public_key(public_key_jwk)
    with _locked_registry(storage_path) as path:
        registry = _read_registry(path)
        if user_id in registry:
            if registry[user_id] != key:
                raise HTTPException(
                    status_code=400, detail="Device key replacement is not allowed."
                )
        else:
            registry[user_id] = key
            _write_registry(path, registry)
    return DeviceRegisterResponse(status="registered")


def get_public_key(user_id: str, *, storage_path: Path | None = None) -> dict[str, str] | None:
    """Retrieve a known user's public JWK for J-06; return None when not registered.

    Unknown users raise HTTP 404; corrupt storage raises DeviceStorageError. Returns
    a fresh object without exposing a new public endpoint or granting execution.
    """
    get_accounts(user_id)
    with _locked_registry(storage_path) as path:
        return _read_registry(path).get(user_id)
