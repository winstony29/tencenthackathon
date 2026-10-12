"""Shared test fixtures: test client and fake Jev, LLM and ASR.

Owner: Winston (shared)
Diagram box: none
Contract: none
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
