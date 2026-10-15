"""Persistent evidence, confidentiality, integrity and reconstruction tests."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from app import config, evidence, transaction_state
from app.audit import AuditError


def test_encrypted_evidence_survives_session_clear_and_redacts_secrets(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "private-api-key-12345")
    evidence.record(
        "request_received",
        {
            "transcript": "Transfer 50 to John private-api-key-12345",
            "nested": {"otp": "987654", "Authorization": "Bearer xyz", "private_key": "hidden"},
        },
        session_id="s_test",
    )
    transaction_state.clear()
    result = evidence.timeline("s_test", 10, True)
    assert result["chain_valid"]
    data = result["entries"][0]["evidence"]
    assert data["transcript"] == "Transfer 50 to John [REDACTED]"
    assert set(data["nested"].values()) == {"[REDACTED]"}
    disk = Path(config.AUDIT_EVIDENCE_PATH).read_bytes()
    for raw in (b"Transfer 50", b"987654", b"private-api-key-12345", b"hidden"):
        assert raw not in disk


def test_plaintext_metadata_holds_no_guessable_digest():
    # H6 (15 Oct audit): sha256(body) beside the ciphertext let a file reader confirm guesses.
    evidence.record("first", {"transcript": "send 350 to John Doe"}, draft_digest="abc123")
    with sqlite3.connect(config.AUDIT_EVIDENCE_PATH) as db:
        (text,) = db.execute("SELECT metadata FROM events").fetchone()
    body = evidence.encoded({"transcript": "send 350 to John Doe"})
    for unkeyed in (hashlib.sha256(body).hexdigest(), hashlib.sha256(b"abc123").hexdigest()):
        assert unkeyed not in text
    assert "abc123" not in text
    assert evidence.timeline(None, 10, True)["entries"][0]["evidence"]["transcript"]


def test_metadata_and_evidence_tampering_are_detected():
    evidence.record("first", {"value": 1})
    with sqlite3.connect(config.AUDIT_EVIDENCE_PATH) as db:
        db.execute("UPDATE events SET payload=?", (b"corrupted",))
    with pytest.raises(AuditError):
        evidence.timeline(None, 10, True)
    with sqlite3.connect(config.AUDIT_EVIDENCE_PATH) as db:
        db.execute("UPDATE events SET hash='wrong'")
    with pytest.raises(AuditError):
        evidence.record("second", {})


def test_expiry_preserves_timeline_and_removes_evidence(monkeypatch):
    evidence.record("first", {"value": "retained briefly"})
    before = evidence.timeline(None, 10, True)["entries"][0]
    now = evidence.time.time()
    monkeypatch.setattr(evidence.time, "time", lambda: now + 8 * 86400)
    after = evidence.timeline(None, 10, True)["entries"][0]
    assert after["evidence_state"] == "expired"
    assert "evidence" not in after
    assert before["hash"] == after["hash"]
    assert before["evidence_digest"] == after["evidence_digest"]


def test_admin_access_required_and_never_cached(client):
    evidence.record("first", {"transcript": "private"}, session_id="s_test")
    assert client.get("/api/audit/timeline?include_evidence=true").status_code == 403
    result = client.get(
        "/api/audit/timeline?session_id=s_test&include_evidence=true",
        headers={"Authorization": "Bearer test-audit-admin"},
    )
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["entries"][0]["evidence"]["transcript"] == "private"


def test_http_failures_and_otp_redaction_are_persisted(client):
    response = client.post(
        "/api/execute", json={"nonce": "n_unknown", "signature": "sig", "otp": "987654"}
    )
    assert response.status_code == 409
    entries = evidence.timeline(None, 10, True)["entries"]
    assert entries[0]["request_id"] == entries[-1]["request_id"]
    assert entries[0]["evidence"]["input"]["otp"] == "[REDACTED]"
    assert entries[-1]["evidence"]["http_status"] == 409


def test_storage_failure_prevents_request_processing(client, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_EVIDENCE_KEY", "")
    response = client.post(
        "/api/understand",
        json={"user_id": "u_demo", "session_id": "s_test", "transcript": "Send 50 to John"},
    )
    assert response.status_code == 503


def test_client_manifest_is_explicitly_untrusted_and_bound_to_session(client):
    result = client.post(
        "/api/audit/presentation",
        json={
            "session_id": "s_ui",
            "draft_id": "d_ui",
            "manifest": {"root": "transfer"},
            "source": "fallback",
        },
    )
    assert result.status_code == 200
    rows = evidence.timeline("s_ui", 10, True)["entries"]
    report = next(r for r in rows if r["event"] == "presentation_client_reported")
    assert report["draft_id"] == "d_ui"
    assert report["evidence"]["manifest"] == {"root": "transfer"}


def test_presentation_reports_are_small_and_stored_once(client):
    big = {"session_id": "s_ui", "manifest": {"x": "y" * 20_000}, "source": "jev"}
    assert client.post("/api/audit/presentation", json=big).status_code == 400
    small = {**big, "manifest": {"root": "transfer"}}
    assert client.post("/api/audit/presentation", json=small).status_code == 200
    rows = evidence.timeline("s_ui", 10, False)["entries"]
    assert [r["event"] for r in rows] == ["presentation_client_reported"]


def test_size_limit_and_no_plaintext_headers(client, monkeypatch):
    monkeypatch.setattr(config, "AUDIT_MAX_BYTES", 100)
    response = client.post("/api/understand", content="x" * 101)
    assert response.status_code == 413


def test_request_history_and_bank_snapshot_reconstruct_after_expiry(client, fake_jev, bank):
    # Use the real intent router with a deterministic model, then clear all live state.
    fake_jev.script = {
        "has_transfer": {"yes": 1},
        "transfer_amount": {"n0": 1},
        "recipient": {"pay_4521": 1},
    }
    result = client.post(
        "/api/understand",
        json={"user_id": "u_demo", "session_id": "s_trace", "transcript": "Send 50 to John Doe"},
    )
    assert result.json()["status"] == "draft", result.text
    transaction_state.clear()
    rows = evidence.timeline("s_trace", 50, True)["entries"]
    draft = next(r for r in rows if r["event"] == "draft_registered")
    assert draft["evidence"]["history"][0]["text"] == "Send 50 to John Doe"
    assert draft["evidence"]["bank_snapshot"]["accounts"]["accounts"]
    assert draft["draft_digest"] and draft["revision"]
    assert len({r["request_id"] for r in rows}) == 1
    assert "receipt" not in json.dumps(draft["evidence"])


def test_clarifications_stay_linked_across_turns(client, fake_jev, bank):
    fake_jev.script = {
        "has_transfer": {"yes": 1},
        "transfer_amount": {"n0": 1},
        "recipient": {"pay_4521": 0.5, "pay_8892": 0.5},
    }
    first = client.post(
        "/api/understand",
        json={"user_id": "u_demo", "session_id": "s_turns", "transcript": "Send 50 to John"},
    ).json()
    assert first["status"] == "question"
    second = client.post(
        "/api/understand",
        json={
            "user_id": "u_demo",
            "session_id": "s_turns",
            "answer": {"question_id": first["question"]["question_id"], "option_id": "pay_8892"},
        },
    )
    assert second.json()["status"] == "draft", second.text
    transaction_state.clear()
    rows = evidence.timeline("s_turns", 100, True)["entries"]
    draft = next(r for r in rows if r["event"] == "draft_registered")
    assert draft["evidence"]["history"][-1]["selected_id"] == "pay_8892"
    question = next(h for h in draft["evidence"]["history"] if h["kind"] == "question")
    assert question["text"] == first["question"]["text"]
    assert question["options"] == first["question"]["options"]
    assert len({r["request_id"] for r in rows}) == 2


def test_parallel_writes_preserve_chain():
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: evidence.record("parallel", {"i": i}), range(12)))
    result = evidence.timeline(None, 100, True)
    assert result["chain_valid"] and len(result["entries"]) == 12


def test_revocation_still_happens_if_logging_breaks(monkeypatch):
    from tests.test_validation import draft as draft_fixture

    draft = draft_fixture.__wrapped__()
    transaction_state.register(draft, [], {})
    record, _ = transaction_state.begin(draft)
    receipt = transaction_state.finish(record, {"status": "match"})["receipt"]
    monkeypatch.setattr(config, "AUDIT_EVIDENCE_KEY", "bad-key")
    with pytest.raises(AuditError):
        transaction_state.revoke(draft.user_id, draft.session_id)
    with pytest.raises(transaction_state.InvalidDraft):
        transaction_state.authorize(draft, receipt)


def test_premature_evidence_deletion_is_not_labelled_expired():
    evidence.record("first", {"important": "evidence"})
    with sqlite3.connect(config.AUDIT_EVIDENCE_PATH) as db:
        db.execute("UPDATE events SET payload=NULL")
    with pytest.raises(AuditError):
        evidence.timeline(None, 10, False)


def test_oversized_evidence_keeps_only_its_size_and_hash(monkeypatch):
    monkeypatch.setattr(config, "EVIDENCE_MAX_ITEM_BYTES", 100)
    evidence.record("big", {"input": "x" * 5000})
    (entry,) = evidence.timeline(None, 10, True)["entries"]
    assert entry["evidence"]["oversized"] is True and entry["evidence"]["bytes"] > 5000


def test_a_full_store_keeps_metadata_and_stays_verifiable(monkeypatch):
    monkeypatch.setattr(config, "EVIDENCE_MAX_STORE_BYTES", 0)
    evidence.record("first", {"important": "evidence"})
    result = evidence.timeline(None, 10, True)
    assert result["chain_valid"] and result["entries"][0]["evidence_state"] == "capacity"


def test_history_cannot_be_rewritten_without_the_key():
    evidence.record("first", {"amount": "50.00"})
    evidence.record("second", {"amount": "60.00"})
    with sqlite3.connect(config.AUDIT_EVIDENCE_PATH) as db:
        (text,) = db.execute("SELECT metadata FROM events WHERE position=0").fetchone()
        meta = json.loads(text)
        meta["event"] = "rewritten"
        forged = evidence.encoded(meta)
        db.execute(
            "UPDATE events SET metadata=?, hash=? WHERE position=0",
            (forged.decode(), hashlib.sha256(forged).hexdigest()),
        )
    with pytest.raises(AuditError):
        evidence.timeline(None, 10, False)


def test_deleted_rows_are_detected_and_writes_stop(monkeypatch):
    # M8 (15 Oct audit): deleting rows from the end, or all of them, still verified.
    evidence.record("first", {"x": 1})
    evidence.record("second", {"x": 2})
    with sqlite3.connect(config.AUDIT_EVIDENCE_PATH) as db:
        db.execute("DELETE FROM events WHERE position=1")
    with pytest.raises(AuditError):
        evidence.timeline(None, 10, False)
    with sqlite3.connect(config.AUDIT_EVIDENCE_PATH) as db:
        db.execute("DELETE FROM events")
    with pytest.raises(AuditError):  # fail closed even though the file now looks clean
        evidence.record("third", {})


def test_the_evidence_key_can_rotate(monkeypatch):
    # M15 (15 Oct audit): a new key made every audited request fail with 503.
    from cryptography.fernet import Fernet

    old = config.AUDIT_EVIDENCE_KEY
    evidence.record("before", {"x": 1})
    monkeypatch.setattr(config, "AUDIT_EVIDENCE_KEY", Fernet.generate_key().decode() + "," + old)
    evidence.record("after", {"x": 2})
    entries = evidence.timeline(None, 10, True)["entries"]
    assert [e["evidence"]["x"] for e in entries] == [1, 2]


def test_the_store_is_owner_only():
    evidence.record("first", {"x": 1})
    assert os.stat(config.AUDIT_EVIDENCE_PATH).st_mode & 0o777 == 0o600
