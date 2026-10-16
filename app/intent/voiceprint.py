"""Speaker verification: is this clip the enrolled voice? (D-054)

Owner: Winston
Diagram box: Speech to text (Intent detection column)
Contract: the `speaker` object in CONTRACTS §3.1; called by app/intent/router.py before ASR

A 256-dimension speaker embedding (sherpa-onnx, WeSpeaker ResNet34 VoxCeleb) is compared by
cosine similarity with the user's enrolled mean embedding. The first speaker after a reset
enrols `u_demo`, like device keys. Any failure of the model is "unavailable" and the request
continues: a dead model file never takes the demo down; the device key still gates execution.
"""

import asyncio
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import urllib.request
import wave
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from app import config, evidence

# ponytail: one embedding at a time; the extractor is not thread-safe and this is a demo.
_lock = asyncio.Lock()
_extractor: Any = None


def _store_path() -> Path:
    if config.VOICEPRINT_PATH:
        return Path(config.VOICEPRINT_PATH)
    return Path(__file__).resolve().parents[2] / "var" / "voiceprints.json"


def _model_path() -> Path:
    name = Path(config.VOICE_MODEL_URL).name
    return Path(__file__).resolve().parents[2] / "var" / "models" / name


def ensure_model() -> Path:
    """Download the speaker model once into var/models and refuse it unless its hash matches."""
    path = _model_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with (
            urllib.request.urlopen(config.VOICE_MODEL_URL, timeout=120) as response,  # noqa: S310
            tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary,
        ):
            shutil.copyfileobj(response, temporary)
        os.replace(temporary.name, path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != config.VOICE_MODEL_SHA256:
        path.unlink(missing_ok=True)
        raise RuntimeError("Speaker model hash mismatch; file removed")
    return path


def _get_extractor() -> Any:
    global _extractor
    if _extractor is None:
        import sherpa_onnx  # lazy: optional on machines that only type

        settings = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(ensure_model()), num_threads=2, provider="cpu"
        )
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(settings)
    return _extractor


def preload() -> None:
    """Fetch the model and load the extractor at startup (app/main.py)."""
    _get_extractor()


def _embed(wav_bytes: bytes) -> list[float]:
    """Unit-length embedding of a 16 kHz mono 16-bit WAV. Runs in a worker thread."""
    with wave.open(BytesIO(wav_bytes)) as wav:
        frames = wav.readframes(wav.getnframes())
    samples = [s / 32768 for s in struct.unpack(f"<{len(frames) // 2}h", frames)]
    extractor = _get_extractor()
    stream = extractor.create_stream()
    stream.accept_waveform(16000, samples)
    stream.input_finished()
    return _unit(list(extractor.compute(stream)))


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _read() -> dict[str, Any]:
    try:
        return json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return {}  # an unreadable store means "no voiceprint" and re-enrols (D-054)


def _write(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False) as handle:
        json.dump(store, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, path)


async def check(user_id: str, wav_bytes: bytes) -> dict[str, Any]:
    """{"status": enrolled | verified | rejected | unavailable, "score": float | None}."""
    if not config.VOICEPRINT_ENABLED:
        return {"status": "unavailable", "score": None}
    try:
        async with _lock:
            vector = await asyncio.wait_for(
                asyncio.to_thread(_embed, wav_bytes), config.VOICEPRINT_TIMEOUT_SECONDS
            )
    except Exception as error:  # fail open (D-054); the reason goes to evidence, never audio
        evidence.record("speaker_unavailable", {"reason": type(error).__name__})
        return {"status": "unavailable", "score": None}
    store = _read()
    entry = store.get(user_id)
    if entry is None:
        store[user_id] = {
            "embedding": vector,
            "clips": 1,
            "enrolled_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        _write(store)
        return {"status": "enrolled", "score": None}
    score = sum(a * b for a, b in zip(entry["embedding"], vector, strict=True))
    if score < config.VOICE_THRESHOLD:
        return {"status": "rejected", "score": round(score, 2)}
    if entry["clips"] < config.VOICE_MAX_CLIPS:  # fold the clip into the running mean
        n = entry["clips"]
        pairs = zip(entry["embedding"], vector, strict=True)
        entry["embedding"] = _unit([(m * n + v) / (n + 1) for m, v in pairs])
        entry["clips"] = n + 1
        _write(store)
    return {"status": "verified", "score": round(score, 2)}


def reset(user_id: str) -> bool:
    """Forget a user's voiceprint so the next speaker enrols. Returns whether one existed."""
    store = _read()
    if store.pop(user_id, None) is None:
        return False
    _write(store)
    return True


if __name__ == "__main__":  # python -m app.intent.voiceprint reset u_demo
    if len(sys.argv) == 3 and sys.argv[1] == "reset":
        print("removed" if reset(sys.argv[2]) else "nothing enrolled")
    else:
        sys.exit("usage: python -m app.intent.voiceprint reset <user_id>")
