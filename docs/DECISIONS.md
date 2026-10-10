# Decisions

**Purpose:** one place for every design choice and its reason, so nobody (human or assistant) re-decides it. Add new entries at the bottom. Never delete an entry; mark it "Replaced by D-0xx" instead.
**Owner:** everyone · **Last updated:** 2026-10-10

Entry format:

```
## D-0xx · Title
Date · Owner · Status: decided | open (due date)
Decision: ...
Why: ...
Rejected: ...
```

---

## D-001 · Python + FastAPI, one server
2026-10-10 · Winston · Status: decided
**Decision:** Python 3.11+ with FastAPI. One server holds all three areas (`app/intent`, `app/validation`, `app/bank`) and serves the front end.
**Why:** Python is the team's strongest language. One server means one deployment and one URL to submit.
**Rejected:** Node + Express; separate services per person (more deployments, nothing gained in 6 days).

## D-002 · One repo, folder ownership, one branch per person
2026-10-10 · Winston · Status: decided
**Decision:** Each person owns folders (`docs/ROLES.md`) and works on one branch named after them. Merges to `main` go through pull requests.
**Why:** Few merge conflicts, and the folders match the architecture diagram.
**Rejected:** Feature branches (too many branches for a 6-day build).

## D-003 · Intent detection: Jev picks, code writes, Jev × K phrasings
2026-10-10 · Winston · Status: decided (results to add after H-01)
**Decision:** Jev only picks from options that code builds; code turns the picks into values. One Jev instance is asked the same questions about the original transcript plus K LLM rewrites. The gate uses the average probability and the spread across versions. "Ensemble" means Jev + the validation agent (two different models), not the K calls.
**Why:** Jev can't invent a value. The spread across rewrites shows when an answer depends on wording.
**Rejected:** One LLM writing the whole draft (can hallucinate values); repeated calls on the same text (likely identical answers; check whether Jev is deterministic).
**Results:** *(add the H-01 table here)*

## D-004 · Front-end stack
2026-10-10 · Darren · Status: **open (due Sun 11 Oct)**
**Decision:** *(Darren fills in)*
**Constraints:** FastAPI must be able to serve the built files; no second deployment; build output not committed.

## D-005 · Fallback if Jev doesn't work
2026-10-10 · Winston · Status: **open (due Sat 10 Oct, 23:00)**
**Decision:** If no real Jev call works by Saturday 23:00, use an LLM with structured output (forced choice from the same option lists) behind the same `jev.py` interface.
**Why:** Nobody else should be blocked by Jev's early-access status.

## D-006 · Audio format for speech to text
2026-10-10 · Winston + Darren · Status: **open (due Mon 12 Oct)**
**Decision:** *(format the browser records vs what Tencent ASR accepts; who converts)*

## D-007 · Risk rule values
2026-10-10 · Jayanth · Status: **open (due Mon 12 Oct)**
**Decision:** *(placeholders: extra check over SGD 5,000; daily limit SGD 20,000; at most 5 transfers per hour; demo OTP code set in config)*
**Why:** The brief asks for velocity controls and an out-of-band check for large amounts. These values are demo placeholders, not DBS policy.

## D-008 · Hosting
2026-10-10 · Darren · Status: **open (due Mon 12 Oct)**
**Decision:** *(Tencent Cloud product, region, how env vars are set on the server)*

## D-009 · LLM provider for rewriter and validator
2026-10-10 · Winston + Jayanth · Status: **open (due Sun 11 Oct)**
**Decision:** *(provider and model names)*
**Note:** Use a different model for the validator than for the rewriter, so the two engines are less likely to make the same mistake. One option is a Tencent-hosted model through an OpenAI-compatible endpoint, which also counts as a Tencent tool. This option has not been checked yet.

## D-010 · Signing with a WebCrypto device key
2026-10-10 · Darren + Jayanth · Status: decided
**Decision:** The browser creates a non-extractable ECDSA P-256 key and signs the server's `signing_payload`. WebAuthn passkeys (Face ID / fingerprint) are a stretch goal.
**Why:** Same demo property (the private key can't be read by page code or the server) with far less setup. Honest limitation: the key is not hardware-backed; say so if judges ask.
**Rejected for now:** WebAuthn (attestation and setup cost too high before Tuesday).

## D-011 · Validation receipt before a nonce
2026-10-10 · Jayanth · Status: decided
**Decision:** `/api/validate` returns an HMAC receipt on a match; `/api/challenge` refuses to issue a nonce without a valid receipt.
**Why:** A frozen (mismatched) draft can never be signed, even if the UI skips the check.

## D-012 · The UI orchestrates; the two sides never call each other
2026-10-10 · Winston · Status: decided
**Decision:** The front end calls the intent, validation and bank endpoints in turn. Server code on the AI side never calls the bank side, and the reverse. The only exception is `app/bank/readonly.py`.
**Why:** This makes the trust boundary on the architecture slide true in the code, and `tests/test_boundary.py` checks it.

## D-013 · Starting Python packages, pinned
2026-10-10 · Winston · Status: decided
**Decision:** `requirements.txt` pins exact versions of: `fastapi`, `uvicorn` (server), `pydantic` (contracts), `python-multipart` (file upload on `/api/transcribe`), `httpx` (external calls and the test client), `python-dotenv` (loads `.env` in `app/config.py`), `pytest`, `ruff`. Everyone installs from this file, so every laptop and the server run the same versions.
**Why:** The minimum S-02 needs. Pinned versions stop "works on my machine" bugs.
**Rejected for now:** `cryptography` (add with J-06, signature checks); `pydantic-settings` (`os.getenv` is enough for a flat list of variables).

## D-014 · Daily pull requests with merge commits
2026-10-10 · Winston · Status: decided
**Decision:** Keep one branch per person (D-002). Each person opens a PR into `main` at least once a day; PRs are merged with "Create a merge commit". After any merge, everyone merges `origin/main` into their own branch. Integration happens continuously on `main`, not in one big merge. Commands are in `docs/GITHUB.md`.
**Why:** Folder ownership already keeps people apart, so per-task branches add naming and cleanup with nothing gained. Squash merging breaks long-lived branches (the same changes conflict again on the next merge).
**Rejected:** A new branch per task (more git to learn, more branches to track); squash or rebase merging.
