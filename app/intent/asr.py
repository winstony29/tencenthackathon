"""Speech to text: the only client for Tencent Cloud ASR.

Owner: Winston
Diagram box: Speech to text (Intent detection column)
Contract: none; called by app/intent/router.py for /api/transcribe; audio format in D-006

The international-site API (asr.intl.tencentcloudapi.com, 2019-06-14) offers only
file-upload recognition: CreateRecTask + DescribeTaskStatus polling. There is no
SentenceRecognition (one-shot) on the intl site. TC3-HMAC-SHA256 signing is
implemented in-house (~30 lines); no SDK dependency is added (AGENTS.md §new deps).

config.ASR_PROVIDER adds MERaLiON-2 (A*STAR's Singlish audio LLM) as a second engine that
only runs locally on Apple Silicon through mlx-meralion, for comparison (D-053).
"""

import asyncio
import base64
import difflib
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from wave import Error as WaveError
from wave import open as wave_open

import httpx

from app import config, evidence
from app.bank import readonly
from app.intent import options

# Fixed Singapore + DBS vocabulary sent as hotwords (weight 8). The command verbs are there
# because 16k_zh_large heard "send fifty" as "seven fifty" without them (Winston's voice, 16 Oct).
_FIXED_HOTWORDS = [
    "Send",
    "Pay",
    "Transfer",
    "Buy",
    "Sell",
    "Convert",
    "DBS",
    "POSB",
    "PayNow",
    "PayLah",
    "GIRO",
    "NETS",
    "NRIC",
    "SGD",
    "USD",
    "Multiplier",
]


class AsrError(Exception):
    """An ASR failure that maps to an HTTP error in the router."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def validate_wav(data: bytes) -> float:
    """Return the audio duration in seconds, or raise AsrError(400) if the WAV is invalid.

    Requires PCM, 1 channel, 16000 Hz, 2 bytes per sample, within the configured
    duration range. The 2 MB size cap is enforced earlier in app/main.py.
    """
    try:
        with wave_open(BytesIO(data)) as wav:
            if wav.getcomptype() != "NONE":
                raise AsrError(400, "bad_request", "Please record again.")
            if wav.getnchannels() != 1:
                raise AsrError(400, "bad_request", "Please record again.")
            if wav.getframerate() != 16000:
                raise AsrError(400, "bad_request", "Please record again.")
            if wav.getsampwidth() != 2:
                raise AsrError(400, "bad_request", "Please record again.")
            frames = wav.getnframes()
            if frames == 0:
                raise AsrError(400, "bad_request", "Please record again.")
            duration = frames / 16000
    except (WaveError, EOFError) as error:
        raise AsrError(400, "bad_request", "Please record again.") from error
    if duration < config.ASR_MIN_SECONDS or duration > config.ASR_MAX_SECONDS:
        raise AsrError(400, "bad_request", "Please record again.")
    return duration


def _hotwords(user_id: str) -> str:
    """Build the hotword string for Tencent ASR from the user's bank data.

    Format: "term|weight,term|weight". Max 128 terms, each ≤30 chars, weight 1–11.
    Strips "," and "|" from each term; deduplicates case-insensitively.
    Priority when over 128: payees, billers, fixed terms, quotes, then account labels.
    """
    try:
        accounts = readonly.get_accounts(user_id)
    except Exception:
        accounts = None
    try:
        quotes = readonly.list_quotes()
    except Exception:
        quotes = []

    payees: list[str] = []
    billers: list[str] = []
    labels: list[str] = []
    if accounts is not None:
        names = [p.name for p in accounts.payees]
        # Whole names first, then each word: the intent engine matches payees word by word,
        # and live ASR heard "Jon" for "John" when only "John Doe" was listed.
        payees = names + [p.label for p in accounts.payees] + [w for n in names for w in n.split()]
        billers = [b.name for b in accounts.billers]
        labels = [a.label for a in accounts.accounts]
    stocks = [q.company_name for q in quotes] + [q.ticker for q in quotes]

    seen: set[str] = set()
    pairs: list[str] = []

    def add(terms: list[str], weight: int) -> None:
        for term in terms:
            # Drop masked digit tails like "…4521": never spoken, and they are account fragments.
            cleaned = re.sub(r"\s*…\S*", "", term)
            cleaned = re.sub(r"[,|]", "", cleaned).strip()[:30]
            if not cleaned or cleaned.lower() in seen:
                continue
            seen.add(cleaned.lower())
            pairs.append(f"{cleaned}|{weight}")

    add(payees, 10)
    add(billers, 10)
    add(_FIXED_HOTWORDS, 8)
    add(stocks, 8)
    add(labels, 10)
    return ",".join(pairs[:128])


def _canonical_request(
    method: str,
    uri: str,
    headers: dict[str, str],
    body: bytes,
) -> tuple[str, str, str]:
    """Return (canonical_request, signed_headers, payload_hash) for TC3 signing.

    canonical_request = method + newline + uri + newline + empty query + newline
    + canonical_headers + newline + signed_headers + newline + payload_hash
    """
    payload_hash = hashlib.sha256(body).hexdigest()
    # Only content-type and host are signed; Tencent lowercases both key and value.
    signed = {k.lower(): v.strip().lower() for k, v in headers.items()}
    signed = {k: signed[k] for k in ("content-type", "host")}
    signed_headers = ";".join(signed)
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in signed.items())
    canonical_request = f"{method}\n{uri}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    return canonical_request, signed_headers, payload_hash


def _sign(
    secret_id: str,
    secret_key: str,
    timestamp: int,
    service: str,
    method: str,
    uri: str,
    headers: dict[str, str],
    body: bytes,
) -> str:
    """TC3-HMAC-SHA256 signature for Tencent Cloud international-site APIs.

    Credential scope: {date}/{service}/tc3_request where date is derived from the
    timestamp in UTC as YYYY-MM-DD. The key chain is:
    HMAC("TC3"+secret_key, date) → HMAC(·, service) → HMAC(·, "tc3_request").
    """
    date = datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d")
    canonical_request, signed_headers, payload_hash = _canonical_request(method, uri, headers, body)
    credential_scope = f"{date}/{service}/tc3_request"
    canonical_request_hash = hashlib.sha256(canonical_request.encode()).hexdigest()
    algorithm = "TC3-HMAC-SHA256"
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{canonical_request_hash}"
    secret_date = hmac.new(("TC3" + secret_key).encode(), date.encode(), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode(), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return authorization


def _build_headers(action: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    """Build signed request headers for a Tencent Cloud API call."""
    secret_id = config.TENCENTCLOUD_SECRET_ID
    secret_key = config.TENCENTCLOUD_SECRET_KEY
    if not secret_id or not secret_key:
        raise AsrError(
            503,
            "upstream_error",
            "Voice isn't set up on this server. Please type your request.",
        )
    if timestamp is None:
        timestamp = int(time.time())
    headers = {
        "Host": config.ASR_HOST,
        "Content-Type": "application/json; charset=utf-8",
        "X-TC-Action": action,
        "X-TC-Version": config.ASR_VERSION,
        "X-TC-Timestamp": str(timestamp),
    }
    if config.TENCENTCLOUD_REGION:
        headers["X-TC-Region"] = config.TENCENTCLOUD_REGION
    uri = "/"
    authorization = _sign(secret_id, secret_key, timestamp, "asr", "POST", uri, headers, body)
    headers["Authorization"] = authorization
    return headers


async def _post(client: httpx.AsyncClient, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the Tencent ASR API and return the Response object."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    headers = _build_headers(action, body)
    response = await client.post(f"https://{config.ASR_HOST}/", content=body, headers=headers)
    return response.json().get("Response", {})


def _upstream(reason: str, result: dict[str, Any]) -> AsrError:
    """Record why Tencent failed (its error code, never audio or keys) and build the 502."""
    evidence.record(
        "asr_failed", {"reason": reason, "code": result.get("Error", {}).get("Code", "")}
    )
    return AsrError(502, "upstream_error", "I couldn't hear that. Please try again or type.")


async def _create_task(
    client: httpx.AsyncClient, audio_b64: str, data_len: int, hotwords: str
) -> int:
    """Create a recognition task and return its TaskId (an integer; DescribeTaskStatus
    rejects a string)."""
    payload = {
        "EngineModelType": config.ASR_ENGINE,
        "ChannelNum": 1,
        "ResTextFormat": 0,
        "SourceType": 1,
        "Data": audio_b64,
        "DataLen": data_len,
        "HotwordList": hotwords,
    }
    result = await _post(client, "CreateRecTask", payload)
    if "Error" in result:
        raise _upstream("create_error", result)
    task_id = result.get("Data", {}).get("TaskId")
    if not isinstance(task_id, int):
        raise _upstream("no_task_id", result)
    return task_id


def _clean_result(result: str) -> str:
    """Drop the "[0:0.000,0:1.880]  " segment prefix ResTextFormat 0 puts on each line."""
    lines = (re.sub(r"^\[[^\]]*\]\s*", "", line).strip() for line in result.splitlines())
    return " ".join(line for line in lines if line)


async def _poll_task(client: httpx.AsyncClient, task_id: int, deadline: float) -> str:
    """Poll DescribeTaskStatus until the task succeeds or fails, or the deadline passes."""
    while True:
        result = await _post(client, "DescribeTaskStatus", {"TaskId": task_id})
        if "Error" in result:
            raise _upstream("poll_error", result)
        data = result.get("Data", {})
        status = data.get("Status")
        if status == 2:
            return _clean_result(data.get("Result", "") or "")
        if status == 3:
            raise _upstream("task_failed", result)
        if time.monotonic() >= deadline:
            raise _upstream("timeout", result)
        await asyncio.sleep(config.ASR_POLL_SECONDS)


async def _tencent(user_id: str, audio_bytes: bytes) -> dict[str, Any]:
    """Tencent's transcript (scrubbed) with the engine and timings."""
    if not config.TENCENTCLOUD_SECRET_ID or not config.TENCENTCLOUD_SECRET_KEY:
        raise AsrError(
            503,
            "upstream_error",
            "Voice isn't set up on this server. Please type your request.",
        )
    audio_b64 = base64.b64encode(audio_bytes).decode()
    hotwords = _hotwords(user_id)
    create_start = time.monotonic()
    deadline = create_start + config.ASR_TIMEOUT_SECONDS  # create + polling share one budget
    async with httpx.AsyncClient(timeout=config.ASR_TIMEOUT_SECONDS) as client:
        task_id = await _create_task(client, audio_b64, len(audio_bytes), hotwords)
        create_ms = round((time.monotonic() - create_start) * 1000)
        poll_start = time.monotonic()
        transcript = evidence.scrub(await _poll_task(client, task_id, deadline))
        poll_ms = round((time.monotonic() - poll_start) * 1000)
    return {
        "transcript": transcript,
        "engine": config.ASR_ENGINE,
        "create_ms": create_ms,
        "poll_ms": poll_ms,
    }


# --- MERaLiON (local Mac only; D-053) ---

_meralion_model: Any = None
# ponytail: one MLX inference at a time (the model isn't thread-safe); fine for a demo.
_meralion_lock = asyncio.Lock()
_SPEAKER_LABEL = re.compile(r"^<[^>]{1,40}>\s*:?\s*")


def _load_meralion() -> tuple[Any, Any]:
    """Import mlx_meralion lazily (not installed on the Linux server) and load the model once."""
    global _meralion_model
    try:
        import mlx_meralion
    except ImportError as error:
        raise AsrError(
            503, "upstream_error", "MERaLiON isn't installed on this machine."
        ) from error
    if _meralion_model is None:
        _meralion_model = mlx_meralion.load_model(config.MERALION_MODEL, verbose=False)
    return mlx_meralion, _meralion_model


def preload() -> None:
    """Load the weights at startup (app/main.py) and run one inference on a second of
    silence: MLX compiles its kernels on the first call, which took over 12 s live."""
    _load_meralion()
    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        with wave_open(os.fdopen(fd, "wb")) as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)
        _meralion_file(path)
    finally:
        with suppress(FileNotFoundError):
            os.remove(path)


def _meralion_file(path: str) -> str:
    """Transcribe one WAV file; runs in a worker thread. Only the built-in ASR task, with no
    custom instruction and no hotwords: an audio LLM follows what it hears (D-053)."""
    module, model = _load_meralion()
    return module.transcribe(model, path, task="asr")


async def _meralion(audio_bytes: bytes) -> dict[str, Any]:
    """MERaLiON's transcript (scrubbed) with its timing. The WAV is written to a temp file
    for mlx_meralion and deleted before returning, success or not."""
    start = time.monotonic()
    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(audio_bytes)
        async with _meralion_lock:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_meralion_file, path), config.MERALION_TIMEOUT_SECONDS
            )
    except TimeoutError as error:
        raise AsrError(
            502, "upstream_error", "I couldn't hear that. Please try again or type."
        ) from error
    finally:
        with suppress(FileNotFoundError):
            os.remove(path)
    text = _SPEAKER_LABEL.sub("", raw.strip()).strip().strip("\"'").strip()
    if len(text) > config.MAX_TEXT_CHARS:  # rambling, or talked into something
        raise AsrError(502, "upstream_error", "I couldn't hear that. Please try again or type.")
    return {
        "transcript": evidence.scrub(text),
        "engine": "meralion",
        "meralion_ms": round((time.monotonic() - start) * 1000),
    }


def normalised(text: str) -> list[str]:
    """Lower-case words with every spoken number as digits, so "Send fifty to John." and
    "send 50 to john" compare equal (Tencent writes digits, MERaLiON writes words)."""
    for number in reversed(options.parse_numbers(text)):
        start, end = number.span
        text = f"{text[:start]}{number.value}{text[end:]}"
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())  # keeps 50.50, drops "john."


def similarity(a: str, b: str) -> float:
    """Word-sequence similarity in 0–1, ignoring case, punctuation and number spelling."""
    return difflib.SequenceMatcher(None, normalised(a), normalised(b)).ratio()


async def transcribe(
    user_id: str, audio_bytes: bytes, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Transcribe a WAV audio blob with the configured provider. `extra` joins the evidence
    record (the router passes the speaker check result, D-054).

    Returns {"transcript": str, "confidence": None}; confidence is always null (D-006).
    In "compare" mode Tencent's transcript is returned and MERaLiON's goes to evidence only.
    """
    duration = validate_wav(audio_bytes)
    provider = config.ASR_PROVIDER
    if provider == "meralion":
        result = await _meralion(audio_bytes)
    elif provider == "compare":
        tencent, meralion = await asyncio.gather(
            _tencent(user_id, audio_bytes), _meralion(audio_bytes), return_exceptions=True
        )
        if isinstance(tencent, BaseException):
            raise tencent  # Tencent is the answer; MERaLiON never stands in for it
        result = tencent
        if isinstance(meralion, BaseException):
            result["meralion_error"] = getattr(meralion, "code", type(meralion).__name__)
        else:
            score = similarity(tencent["transcript"], meralion["transcript"])
            result |= {
                "meralion_transcript": meralion["transcript"],
                "meralion_ms": meralion["meralion_ms"],
                "similarity": round(score, 3),
                "agree": score >= config.ASR_AGREE_MIN,
            }
    else:
        result = await _tencent(user_id, audio_bytes)
    evidence.record(
        "transcribed",
        {
            **result,
            **(extra or {}),
            "provider": provider,
            "audio_duration": round(duration, 3),
            "audio_saved": False,
            "word_count": len(result["transcript"].split()),
        },
    )
    return {"transcript": result["transcript"], "confidence": None}
