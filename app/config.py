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

TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")  # direct TypeSafe key; unused (D-005)
MAKERS_MODELS_KEY = os.getenv("MAKERS_MODELS_KEY", "")  # Jev gateway key (D-005)
MAKERS_JEV_ENDPOINT = "https://ai-gateway.edgeone.link/v1/systemone"  # Native System One API
MAKERS_JEV_MODEL = "@makers/jev"  # Gateway model alias
MAKERS_JEV_TIMEOUT_SECONDS = 12  # Presentation request deadline; UI falls back on failure
# HMAC key for the stand-in numbers Jev reads (D-026). A random key per process is enough,
# because sessions live in memory; set it to keep stand-ins stable across restarts.
MASK_KEY = os.getenv("MASK_KEY", "").encode() or secrets.token_bytes(32)
STAND_IN_TRIES = 50  # HMAC retries before two numbers in one text may share a stand-in (D-026)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # OpenAI-compatible endpoint for app/llm.py (D-009)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")  # key for that endpoint
REWRITER_MODEL = os.getenv("REWRITER_MODEL", "")  # model that writes the K rewrites
VALIDATOR_MODEL = os.getenv("VALIDATOR_MODEL", "")  # model that audits drafts; not the rewriter

TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID", "")  # Tencent ASR (app/intent/asr.py)
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY", "")  # Tencent ASR secret
TENCENTCLOUD_REGION = os.getenv("TENCENTCLOUD_REGION", "ap-singapore")  # Tencent ASR region

VALIDATION_RECEIPT_KEY = os.getenv("VALIDATION_RECEIPT_KEY", "") or secrets.token_hex(32)

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

# Persistent audit evidence; key/token must survive restart and stay outside source control.
AUDIT_EVIDENCE_KEY = os.getenv("AUDIT_EVIDENCE_KEY", "")
AUDIT_ADMIN_TOKEN = os.getenv("AUDIT_ADMIN_TOKEN", "")
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "7"))
AUDIT_MAX_BYTES = 2_000_000  # Bound each JSON request/response the audit middleware reads.
EVIDENCE_MAX_ITEM_BYTES = 64_000  # larger evidence keeps only its size and SHA-256
EVIDENCE_MAX_STORE_BYTES = 500_000_000  # past this, new events keep metadata only
AUDIT_EVIDENCE_PATH = os.getenv("AUDIT_EVIDENCE_PATH", "")  # Blank uses var/evidence.sqlite3.

# Request guard (app/main.py): limits for an anonymous public demo. Starting values.
API_DOCS = os.getenv("API_DOCS", "1") == "1"  # 0 when deployed: hides /docs and /openapi.json
MAX_REQUEST_BYTES = 256_000  # largest JSON body any /api/ endpoint accepts
MAX_AUDIO_BYTES = 2_000_000  # largest /api/transcribe upload (a few seconds of speech)
RATE_PER_MINUTE = {"preview": 120, "model": 40}  # model-backed calls per client IP per minute
RATE_GLOBAL_PER_MINUTE = 600  # model-backed calls per minute from everyone together
