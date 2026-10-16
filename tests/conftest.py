"""Shared test fixtures: test client and fake Jev, LLM and ASR.

Owner: Winston (shared)
Diagram box: none
Contract: none
"""

import pytest
from fastapi.testclient import TestClient

from app import audit, config, main, transaction_state
from app.bank import readonly
from app.contracts import AccountsResponse, FxResponse, QuoteResponse
from app.intent import jev, session
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def audit_log(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Intent events go to a temporary audit chain, never the real var/audit.jsonl."""
    # app/audit.py writes next to its own package; tests/test_bank.py redirects it the same way.
    monkeypatch.setattr(audit, "__file__", str(tmp_path / "app" / "audit.py"))
    return tmp_path / "var" / "audit.jsonl"


@pytest.fixture(autouse=True)
def _no_leftover_sessions(_isolated_evidence) -> None:
    # Sessions and rate limits live in memory for the whole process; each test starts clean.
    session._SESSIONS.clear()
    main.RATE_BUCKETS.clear()
    main.RATE_WINDOW.clear()
    transaction_state.clear()


class FakeJev:
    """Stands in for `jev.ask`: tests script the probabilities per question name.

    Unscripted questions answer their neutral option ("none", "no", "default",
    "market", "keep", "prepare") with certainty, so a test only scripts what it is about.
    """

    def __init__(self) -> None:
        self.script: dict[str, dict[str, float]] = {}
        self.calls: list[tuple[str, list[jev.Question]]] = []
        self.contexts: list[dict | None] = []
        self.error: Exception | None = None

    async def ask(
        self, text: str, questions: list[jev.Question], context: dict | None = None
    ) -> jev.Reply:
        self.calls.append((text, questions))
        self.contexts.append(context)
        if self.error:
            raise self.error
        return jev.Reply("jev-fake", {q.name: self._answer(q) for q in questions})

    def _answer(self, question: jev.Question) -> jev.Answer:
        wanted = self.script.get(question.name) or {_neutral(question): 1.0}
        probabilities = {option: float(wanted.get(option, 0.0)) for option in question.criteria}
        choice = max(probabilities, key=probabilities.get)
        return jev.Answer(choice, probabilities[choice], probabilities)


def _neutral(question: jev.Question) -> str:
    for option in ("none", "no", "default", "market", "keep", "prepare"):
        if option in question.criteria:
            return option
    return next(iter(question.criteria))


@pytest.fixture
def fake_jev(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    fake = FakeJev()
    monkeypatch.setattr(config, "MAKERS_MODELS_KEY", "test-key")
    monkeypatch.setattr(jev, "ask", fake.ask)
    return fake


@pytest.fixture(autouse=True)
def _isolated_voiceprints(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test touches var/voiceprints.json or downloads the speaker model (D-054)."""
    monkeypatch.setattr(config, "VOICEPRINT_ENABLED", False)
    monkeypatch.setattr(config, "VOICEPRINT_PATH", str(tmp_path / "voiceprints.json"))


class FakeVoice:
    """Stands in for voiceprint._embed: returns scripted unit vectors, one per call."""

    def __init__(self) -> None:
        self.vectors: list[list[float]] = []
        self.error: Exception | None = None
        self.delay = 0.0
        self.calls = 0

    def embed(self, wav_bytes: bytes) -> list[float]:
        import time as _time

        self.calls += 1
        _time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.vectors.pop(0) if self.vectors else SAME_VOICE


def _unit(*values: float) -> list[float]:
    import math

    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] + [0.0] * (256 - len(values))


SAME_VOICE = _unit(1.0)
CLOSE_VOICE = _unit(0.9, 0.436)  # cosine 0.9 with SAME_VOICE
OTHER_VOICE = _unit(0.0, 1.0)  # cosine 0 with SAME_VOICE


@pytest.fixture
def fake_voice(monkeypatch: pytest.MonkeyPatch) -> FakeVoice:
    from app.intent import voiceprint

    fake = FakeVoice()
    monkeypatch.setattr(config, "VOICEPRINT_ENABLED", True)
    monkeypatch.setattr(voiceprint, "_embed", fake.embed)
    return fake


class FakeAsr:
    """Stands in for Tencent ASR HTTP calls: tests script task statuses and results.

    Monkeypatches ``asr._post`` so no network call is made. ``statuses`` is a list
    of DescribeTaskStatus ``Response.Data`` dicts consumed one per poll; a single
    CreateRecTask always succeeds with TaskId 1.
    """

    def __init__(self) -> None:
        self.statuses: list[dict] = []
        self.result: str = "send fifty to john"
        self.create_error: dict | None = None
        self.calls: list[tuple[str, dict]] = []

    async def post(self, client: object, action: str, payload: dict) -> dict:
        self.calls.append((action, payload))
        if action == "CreateRecTask":
            if self.create_error:
                return {"Error": self.create_error}
            return {"Data": {"TaskId": 1}}
        if action == "DescribeTaskStatus":
            # The real API rejects a string TaskId with InvalidParameter (seen live, 16 Oct).
            assert isinstance(payload["TaskId"], int), "TaskId must be an int"
            if self.statuses:
                data = self.statuses.pop(0)
            elif self.result is not None:
                data = {"Status": 2, "Result": self.result}
                self.result = None
            else:
                data = {"Status": 2, "Result": ""}
            return {"Data": data}
        return {}


@pytest.fixture
def fake_asr(monkeypatch: pytest.MonkeyPatch, bank) -> FakeAsr:
    fake = FakeAsr()
    monkeypatch.setattr(config, "TENCENTCLOUD_SECRET_ID", "test-secret-id")
    monkeypatch.setattr(config, "TENCENTCLOUD_SECRET_KEY", "test-secret-key")
    monkeypatch.setattr(config, "ASR_POLL_SECONDS", 0.0)
    from app.intent import asr

    monkeypatch.setattr(asr, "_post", fake.post)
    return fake


BANK = AccountsResponse.model_validate(
    {
        "accounts": [
            {
                "account_id": "acc_1234",
                "label": "Savings …1234",
                "type": "savings",
                "currency": "SGD",
                "balance": "3200.00",
            },
            {
                "account_id": "acc_5678",
                "label": "Current …5678",
                "type": "current",
                "currency": "SGD",
                "balance": "900.00",
            },
            {
                "account_id": "acc_7788",
                "label": "Invest (USD) …7788",
                "type": "invest",
                "currency": "USD",
                "balance": "25000.00",
            },
        ],
        "payees": [
            {"payee_id": "pay_4521", "name": "John Doe", "label": "John Doe …4521"},
            {"payee_id": "pay_8892", "name": "John Smith", "label": "John Smith …8892"},
            {"payee_id": "pay_3310", "name": "Tan Mei Ling", "label": "Tan Mei Ling …3310"},
            {"payee_id": "pay_0001", "name": "Mum", "label": "Mum …0001"},
        ],
        "billers": [
            {
                "biller_id": "bil_0001",
                "name": "DBS Credit Card",
                "label": "DBS Credit Card …9012",
                "amount_due": "1240.00",
            }
        ],
        "holdings": [{"ticker": "AAPL", "quantity": 10}],
    }
)
QUOTES = [
    QuoteResponse(
        ticker=t, company_name=n, price=p, currency="USD", as_of="2026-10-12T09:30:00+08:00"
    )
    for t, n, p in [("AAPL", "Apple Inc.", "227.50"), ("TSLA", "Tesla Inc.", "251.00")]
]
FX = {("SGD", "USD"): "0.740000", ("USD", "SGD"): "1.351351"}


def _wav_bytes(
    duration: float = 1.0, framerate: int = 16000, channels: int = 1, sampwidth: int = 2
) -> bytes:
    """Return a valid PCM WAV (silence) for ASR tests."""
    from io import BytesIO
    from wave import open as wave_open

    buf = BytesIO()
    with wave_open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sampwidth)
        wav.setframerate(framerate)
        wav.writeframes(b"\x00\x00" * int(framerate * duration))
    return buf.getvalue()


def _fx_rate(from_currency: str, to_currency: str) -> FxResponse:
    if (from_currency, to_currency) not in FX:
        raise LookupError(f"no rate for {from_currency} to {to_currency}")
    return FxResponse.model_validate(
        {
            "from": from_currency,
            "to": to_currency,
            "rate": FX[from_currency, to_currency],
            "as_of": "2026-10-13T09:30:00+08:00",
        }
    )


@pytest.fixture
def bank(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands in for Jayanth's app/bank/readonly.py (CONTRACTS §3.3) until J-02 lands."""
    monkeypatch.setattr(readonly, "get_accounts", lambda user_id: BANK, raising=False)
    monkeypatch.setattr(readonly, "list_quotes", lambda: QUOTES, raising=False)
    monkeypatch.setattr(
        readonly, "get_bill_reference", lambda biller_id: "4120-XXXX-XXXX-9012", raising=False
    )
    monkeypatch.setattr(readonly, "get_fx_rate", _fx_rate, raising=False)


@pytest.fixture(autouse=True)
def _isolated_evidence(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("BANK_EXECUTION_ENABLED", "0")
    monkeypatch.setattr(config, "BANK_EXECUTION_ENABLED", False)
    monkeypatch.setattr(config, "BANK_GATEWAY_POLICY", "")
    monkeypatch.setattr(config, "BANK_TRANSFER_POLICY", "")
    monkeypatch.setattr(config, "TRANSACTION_STATE_PATH", str(tmp_path / "approvals.sqlite3"))

    monkeypatch.setattr(
        config, "AUDIT_EVIDENCE_PATH", str(tmp_path / "evidence" / "evidence.sqlite3")
    )
    monkeypatch.setattr(config, "AUDIT_EVIDENCE_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "AUDIT_ADMIN_TOKEN", "test-audit-admin")
