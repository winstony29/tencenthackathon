"""Settings: reads environment variables and holds every constant.

Owner: Winston (shared)
Diagram box: none; used by every box
Contract: the variables listed in .env.example
"""

import os
import secrets
from decimal import Decimal

from dotenv import load_dotenv

# Real environment variables win over .env, so a server can set them directly.
load_dotenv()


def _strong(name: str, value: str | bytes) -> None:
    """Refuse a short or repetitive key: a receipt key "dcta" was cracked offline from one
    receipt (M6, 15 Oct audit). ponytail: counts distinct characters, not true entropy."""
    if value and (len(value) < 32 or len(set(value)) < 8):
        raise ValueError(
            f"{name} must be at least 32 bytes of random characters "
            "(python -c 'import secrets; print(secrets.token_urlsafe(32))'); "
            "leave it empty for a random one."
        )


TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")  # direct TypeSafe key; unused (D-005)
MAKERS_MODELS_KEY = os.getenv("MAKERS_MODELS_KEY", "")  # Jev gateway key (D-005)
MAKERS_JEV_ENDPOINT = "https://ai-gateway.edgeone.link/v1/systemone"  # Native System One API
MAKERS_JEV_MODEL = "@makers/jev"  # Gateway model alias
MAKERS_JEV_TIMEOUT_SECONDS = 12  # Presentation request deadline; UI falls back on failure
# HMAC key for the stand-in numbers Jev reads (D-026). A random key per process is enough,
# because sessions live in memory; set it to keep stand-ins stable across restarts.
_strong("MASK_KEY", os.getenv("MASK_KEY", ""))  # every stand-in and pseudonym rests on it
MASK_KEY = os.getenv("MASK_KEY", "").encode() or secrets.token_bytes(32)
STAND_IN_TRIES = 50  # HMAC retries before two numbers in one text may share a stand-in (D-026)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # OpenAI-compatible endpoint for app/llm.py (D-009)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")  # key for that endpoint
REWRITER_MODEL = os.getenv("REWRITER_MODEL", "")  # model that writes the K rewrites
VALIDATOR_MODEL = os.getenv("VALIDATOR_MODEL", "")  # model that audits drafts; not the rewriter

TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID", "")  # Tencent ASR (app/intent/asr.py)
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY", "")  # Tencent ASR secret
TENCENTCLOUD_REGION = os.getenv("TENCENTCLOUD_REGION", "ap-singapore")  # Tencent ASR region
# International-site ASR settings (D-006, W-09). The intl API is file-upload + poll.
ASR_ENGINE = "16k_zh_large"  # live 16 Oct: heard "John" in 2 s; "16k_en" heard "Jon" in 6 s
ASR_POLL_SECONDS = 0.3  # how often to poll DescribeTaskStatus
ASR_TIMEOUT_SECONDS = 10  # give up before the frontend's 15 s request timeout
ASR_MIN_SECONDS = 0.3  # shortest WAV the server accepts
ASR_MAX_SECONDS = 15  # longest WAV the server accepts (MAX_AUDIO_BYTES caps size)
ASR_HOST = "asr.intl.tencentcloudapi.com"  # international-site endpoint
ASR_VERSION = "2019-06-14"  # Tencent ASR API version


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.getenv(name, default)
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
    return value


# Second engine for Singlish comparison on a Mac (D-053); the deployed server stays "tencent".
ASR_PROVIDER = _choice("ASR_PROVIDER", "tencent", ("tencent", "meralion", "compare"))
MERALION_MODEL = os.getenv("MERALION_MODEL", "MERaLiON/MERaLiON-2-10B-MLX")  # 3B-MLX for 8 GB Macs
MERALION_TIMEOUT_SECONDS = 12  # under the UI's 15 s fetch timeout
ASR_AGREE_MIN = 0.8  # word-sequence similarity below this counts as disagreement

# Speaker verification on every voice command (D-054); fails open when the model is unavailable.
VOICEPRINT_ENABLED = True
VOICEPRINT_PATH = os.getenv("VOICEPRINT_PATH", "")  # blank uses var/voiceprints.json
VOICEPRINT_TIMEOUT_SECONDS = 5  # ASR's 10 s + this stays under the UI's 15 s fetch timeout
VOICE_THRESHOLD = 0.65  # cosine similarity; from Winston's clips vs synthetic voices, recheck live
VOICE_MAX_CLIPS = 10  # verified clips folded into the enrolled mean
VOICE_MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    "wespeaker_en_voxceleb_resnet34_LM.onnx"
)
VOICE_MODEL_SHA256 = "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012"

_strong("VALIDATION_RECEIPT_KEY", os.getenv("VALIDATION_RECEIPT_KEY", ""))
VALIDATION_RECEIPT_KEY = os.getenv("VALIDATION_RECEIPT_KEY", "") or (
    "" if os.getenv("BANK_EXECUTION_ENABLED", "0") == "1" else secrets.token_hex(32)
)

USE_FAKES = os.getenv("USE_FAKES", "0") == "1"  # True: fake Jev / LLM / ASR instead of real calls

MAX_ACTIONS_PER_DRAFT = 4  # most actions one draft may hold (CONTRACTS §2)
MAX_TEXT_CHARS = 500  # longest text or transcript the server accepts (CONTRACTS §1)
MAX_QUESTIONS = 3  # clarify questions before giving up with "too_many_questions" (CONTRACTS §3.1)
SESSION_TIMEOUT_SECONDS = 300  # a clarify session expires after this long without a call
TIMEZONE = "Asia/Singapore"  # offset written into draft times (CONTRACTS §1)
CLARIFY_MAX_OPTIONS = 4  # most chips one clarifying question shows
CLARIFY_MIN_SHARE = 0.2  # chips need at least this share of the likeliest option's probability
PREVIEW_CACHE_SIZE = 500  # distinct texts /api/preview remembers before dropping the oldest

SHIELD_BLOCK_AT = 0.5  # Jev's probability of an injection at which the shield blocks (W-08)

# Confidence gate (W-05). Starting guesses, tuned with scripts/eval_intent.py in H-01.
GATE_TAU_BASE = 0.80  # least average probability Jev's pick needs to proceed
GATE_TAU_HIGH = 0.90  # the same, once the largest number said is above GATE_HIGH_AMOUNT
GATE_HIGH_AMOUNT = Decimal("1000")  # where the stricter threshold starts
GATE_DELTA = 0.15  # most a pick's probability may vary across phrasings
AUDIT_DEFAULT_LIMIT = 50  # entries GET /api/audit returns when no limit is given (CONTRACTS §3.3)

VALIDATOR_PROVIDER = os.getenv("VALIDATOR_PROVIDER", "codex")  # local demo, or compatible-api
VALIDATOR_CODEX_MODEL = os.getenv("VALIDATOR_CODEX_MODEL", "")  # blank uses CLI default
VALIDATOR_TIMEOUT_SECONDS = 45 if VALIDATOR_PROVIDER == "codex" else 12
VALIDATOR_RETRY_SECONDS = 10  # an "unavailable" check is reused this long before a model rerun

# Persistent audit evidence; key/token must survive restart and stay outside source control.
AUDIT_EVIDENCE_KEY = os.getenv("AUDIT_EVIDENCE_KEY", "")
AUDIT_ADMIN_TOKEN = os.getenv("AUDIT_ADMIN_TOKEN", "")
_strong("AUDIT_ADMIN_TOKEN", AUDIT_ADMIN_TOKEN)
_KEYS = [
    os.getenv(name, "")
    for name in ("MASK_KEY", "VALIDATION_RECEIPT_KEY", "AUDIT_EVIDENCE_KEY", "AUDIT_ADMIN_TOKEN")
]
if len({k for k in _KEYS if k}) < len([k for k in _KEYS if k]):
    raise ValueError("Use a different value for each key and token.")
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "7"))
AUDIT_MAX_BYTES = 2_000_000  # Bound each JSON request/response the audit middleware reads.
EVIDENCE_MAX_ITEM_BYTES = 64_000  # larger evidence keeps only its size and SHA-256
EVIDENCE_MAX_STORE_BYTES = 500_000_000  # past this, new events keep metadata only
AUDIT_EVIDENCE_PATH = os.getenv("AUDIT_EVIDENCE_PATH", "")  # Blank uses var/evidence.sqlite3.

# Bank integration is opt-in; explicit JSON policies contain backend-owned limits.
BANK_EXECUTION_ENABLED = os.getenv("BANK_EXECUTION_ENABLED", "0") == "1"
BANK_SUPERVISED_USER = os.getenv("BANK_SUPERVISED_USER", "")
BANK_GATEWAY_POLICY = os.getenv("BANK_GATEWAY_POLICY", "")
BANK_TRANSFER_POLICY = os.getenv("BANK_TRANSFER_POLICY", "")
TRANSACTION_STATE_PATH = os.getenv("TRANSACTION_STATE_PATH", "")

# Presentation comparisons older than this remain unknown for current decisions.
CONDITION_QUOTE_MAX_AGE_SECONDS = 60
# Request guard (app/main.py): limits for an anonymous public demo. Starting values.
API_DOCS = os.getenv("API_DOCS", "0") == "1"  # 1 locally shows /docs and /openapi.json (L10)
MAX_REQUEST_BYTES = 256_000  # largest JSON body any /api/ endpoint accepts
MAX_AUDIO_BYTES = 2_000_000  # largest /api/transcribe upload (a few seconds of speech)
MAX_MANIFEST_BYTES = 16_000  # largest manifest /api/audit/presentation stores
# Calls per client IP per minute, by group (app/main.py _RATE_GROUPS). "validate" allows the
# UI's 500 ms polling while a check runs; a draft revision still costs at most one model call.
RATE_PER_MINUTE = {"preview": 120, "model": 40, "validate": 120, "write": 60, "read": 120}
# Groups that spend a model or ASR key also share one budget across every client. Each group
# has its own, so a flood of previews can't stop commands.
RATE_GLOBAL_PER_MINUTE = {"preview": 600, "model": 600}
