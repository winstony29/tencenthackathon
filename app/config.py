"""Settings: reads environment variables and holds every constant.

Owner: Winston (shared)
Diagram box: none; used by every box
Contract: the variables listed in .env.example
"""

import os
from decimal import Decimal

from dotenv import load_dotenv

# Real environment variables win over .env, so a server can set them directly.
load_dotenv()

TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")  # direct TypeSafe key; unused (D-005)
MAKERS_MODELS_KEY = os.getenv("MAKERS_MODELS_KEY", "")  # Jev gateway key (D-005)
MAKERS_JEV_ENDPOINT = "https://ai-gateway.edgeone.link/v1/systemone"  # Native System One API
MAKERS_JEV_MODEL = "@makers/jev"  # Gateway model alias
MAKERS_JEV_TIMEOUT_SECONDS = 12  # Presentation request deadline; UI falls back on failure

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # OpenAI-compatible endpoint for app/llm.py (D-009)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")  # key for that endpoint
REWRITER_MODEL = os.getenv("REWRITER_MODEL", "")  # model that writes the K rewrites
VALIDATOR_MODEL = os.getenv("VALIDATOR_MODEL", "")  # model that audits drafts; not the rewriter

TENCENTCLOUD_SECRET_ID = os.getenv("TENCENTCLOUD_SECRET_ID", "")  # Tencent ASR (app/intent/asr.py)
TENCENTCLOUD_SECRET_KEY = os.getenv("TENCENTCLOUD_SECRET_KEY", "")  # Tencent ASR secret
TENCENTCLOUD_REGION = os.getenv("TENCENTCLOUD_REGION", "ap-singapore")  # Tencent ASR region

VALIDATION_RECEIPT_KEY = os.getenv("VALIDATION_RECEIPT_KEY", "")  # HMAC key for receipts (D-011)

USE_FAKES = os.getenv("USE_FAKES", "0") == "1"  # True: fake Jev / LLM / ASR instead of real calls

MAX_ACTIONS_PER_DRAFT = 4  # most actions one draft may hold (CONTRACTS §2)
MAX_TEXT_CHARS = 500  # longest text or transcript the server accepts (CONTRACTS §1)
MAX_QUESTIONS = 3  # clarify questions before giving up with "too_many_questions" (CONTRACTS §3.1)
SESSION_TIMEOUT_SECONDS = 300  # a clarify session expires after this long without a call
TIMEZONE = "Asia/Singapore"  # offset written into draft times (CONTRACTS §1)
CLARIFY_MAX_OPTIONS = 4  # most chips one clarifying question shows
CLARIFY_MIN_PROBABILITY = 0.05  # options below this average probability are not offered as chips
PREVIEW_CACHE_SIZE = 500  # distinct texts /api/preview remembers before dropping the oldest

SHIELD_BLOCK_AT = 0.5  # Jev's probability of an injection at which the shield blocks (W-08)

# Confidence gate (W-05). Starting guesses, tuned with scripts/eval_intent.py in H-01.
GATE_TAU_BASE = 0.80  # least average probability Jev's pick needs to proceed
GATE_TAU_HIGH = 0.90  # the same, once the largest number said is above GATE_HIGH_AMOUNT
GATE_HIGH_AMOUNT = Decimal("1000")  # where the stricter threshold starts
GATE_DELTA = 0.15  # most a pick's probability may vary across phrasings
AUDIT_DEFAULT_LIMIT = 50  # entries GET /api/audit returns when no limit is given (CONTRACTS §3.3)
