"""Shared test fixtures: test client and fake Jev, LLM and ASR.

Owner: Winston (shared)
Diagram box: none
Contract: none
"""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.intent import jev
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class FakeJev:
    """Stands in for `jev.ask`: tests script the probabilities per question name.

    Unscripted questions answer their neutral option ("none", "no", "default",
    "market") with certainty, so a test only scripts what it is about.
    """

    def __init__(self) -> None:
        self.script: dict[str, dict[str, float]] = {}
        self.calls: list[tuple[str, list[jev.Question]]] = []
        self.error: Exception | None = None

    async def ask(self, text: str, questions: list[jev.Question]) -> jev.Reply:
        self.calls.append((text, questions))
        if self.error:
            raise self.error
        return jev.Reply("jev-fake", {q.name: self._answer(q) for q in questions})

    def _answer(self, question: jev.Question) -> jev.Answer:
        wanted = self.script.get(question.name) or {_neutral(question): 1.0}
        probabilities = {option: float(wanted.get(option, 0.0)) for option in question.criteria}
        choice = max(probabilities, key=probabilities.get)
        return jev.Answer(choice, probabilities[choice], probabilities)


def _neutral(question: jev.Question) -> str:
    for option in ("none", "no", "default", "market"):
        if option in question.criteria:
            return option
    return next(iter(question.criteria))


@pytest.fixture
def fake_jev(monkeypatch: pytest.MonkeyPatch) -> FakeJev:
    fake = FakeJev()
    monkeypatch.setattr(config, "MAKERS_MODELS_KEY", "test-key")
    monkeypatch.setattr(jev, "ask", fake.ask)
    return fake
