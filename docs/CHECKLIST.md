# Checklist

**Purpose:** the build sequence. Every task has an ID, an owner, a day, and a "Done when" test. Tick a box only when every "Done when" condition holds.
**Owner:** everyone ticks their own tasks · **Last updated:** 2026-10-10

IDs: `S` setup · `W` Winston · `J` Jayanth · `D` Darren · `I` integration · `H` hardening · `P` submission.

---

## Phase 0: Skeleton (Sat 10 Oct)

- [ ] **S-01** · Winston · Create GitHub repo `dcta` with this docs pack, `.gitignore`, `.env.example`. Create branches `winston`, `darren`, `jayanth` from `main`.
  *Done when:* all three teammates can clone it and see the three branches.
- [x] **S-02** · Winston · Build the skeleton: every file in `docs/STRUCTURE.md` §1 except `web/`, each with its header docstring (`docs/STYLE.md` §1.1). `app/contracts.py` has every model in `docs/CONTRACTS.md`. Every router returns the stub responses from CONTRACTS §7. Add `requirements.txt` and `pyproject.toml`.
  *Done when:* `uvicorn app.main:app --reload` starts, `http://localhost:8000/docs` lists every endpoint, and each one returns its stub.
- [x] **S-03** · Winston · Write `tests/test_boundary.py` (import rules) and `tests/conftest.py` (test client, fake Jev, fake LLM, fake ASR).
  *Done when:* `pytest` passes and the boundary test fails if you add `from app.bank import ledger` to any intent file.
  *Note (12 Oct):* the fake Jev arrives with W-03; the fake LLM and ASR arrive with W-04 and W-09, once their clients exist.
- [ ] **S-04** · Everyone · Clone, create a venv, run the app, install CodeBuddy, take your first screenshot into `docs/screenshots/`.
  *Done when:* each person has committed one screenshot on their branch.

## Phase 1: Build alone against stubs (Sat 10 – Mon 12)

### Winston: intent engine

- [x] **W-01** · Sat · Get one real Jev call working from `app/intent/jev.py`. If it fails by Saturday 23:00, record the fallback in D-005 and switch.
  *Done when:* a script call returns a choice with probabilities, or D-005 says "fallback".
- [x] **W-02** · Sun · `options.py`: build option lists for action type, payee, biller, account, ticker, side, and the number words in the transcript.
  *Done when:* tests show the right options for "send fifty to John" and "buy 100 Apple".
- [x] **W-03** · Sun · `jev.py`: send the full question set for up to 4 actions in one batched call; return probabilities per option.
  *Done when:* the fake-Jev test and one real call both parse correctly.
  *Live (12 Oct):* `LIVE_JEV=1 pytest -k live` passes demo commands 1 to 4 and an off-topic request against `jev-1.13.0`, about 2 s each (shield + batch). Option wording was tuned against live probabilities. The gateway returns HTTP 429 after a few dozen calls in quick succession, and the key is shared with Darren's presentation demo.
- [ ] **W-04** · Sun · `rewriter.py`: K plain rewrites (K from config). The original transcript is always kept as R0.
  *Done when:* the test confirms R0 is unchanged and K + 1 texts come back.
- [x] **W-05** · Sun · `gate.py`: average + spread rule; tau rises with the amount (values in config).
  *Done when:* tests cover proceed, low-average and high-spread cases.
- [x] **W-06** · Mon · `session.py` + clarify loop in `router.py`: question → answer → re-run on narrowed options; "none of these" cancels.
  *Done when:* "send fifty to John" returns a question, and answering `pay_4521` returns a draft.
- [x] **W-07** · Mon · `draft.py`: assemble the draft; amounts from number words ("fifty", "a grand", "50k"), relative amounts ("half of it", "whatever is left") computed by code.
  *Done when:* drafts for demo commands 1, 2 and 4 match CONTRACTS exactly.
  *Note (12 Oct):* number words, digits, "50k", "a grand" and spellings like "fiddy" are parsed. Relative amounts ("half my savings", "everything in current") are offered as fractions of each balance; "whatever is left" means the whole balance, not what remains after other actions.
- [x] **W-08** · Mon · `shield.py`: flag instructions aimed at the system; `/api/understand` returns `blocked`.
  *Done when:* demo command 3 is blocked and command 1 is not.
- [ ] **W-09** · Mon · `asr.py`: Tencent ASR; decide the audio format with Darren (D-006).
  *Done when:* a recorded clip of command 1 transcribes correctly through `/api/transcribe`.
- [ ] **W-10** · Mon · `tests/commands.jsonl`: ~30 commands with expected outcome (draft, question, blocked or unsupported), including slang, accents spelt out, multi-intent and attacks.
  *Done when:* the file has at least 30 lines, and Jayanth has added 5 mismatch cases.
  *Status (12 Oct):* 33 commands written, each with `expect`, `tags` and expected `actions`; `known-gap` tags mark what should work but doesn't yet. Waiting for Jayanth's 5 cases (`"expect": "mismatch"`).
  *Live sweep (12 Oct):* 30 of 33 as expected across the last runs (not one clean full run: the gateway rate-limits). The 3 misses ("sell 5 apple", the Singlish transfer, "half my savings") ask a question instead of drafting; none guesses.
- [x] **W-11** · Mon · `/api/preview`: one Jev call on the raw text for the action type, cached by normalised text; `meta` on every intent reply.
  *Done when:* typing "send fifty to jo" returns `transfer` with the CONTRACTS shape, and a repeat of the same text is served from cache.
- [ ] **W-12** · Tue · Contracts v0.3 for Darren's `/v3`: `objective` on the `draft` reply (`prepare`, `assess_affordability`, `simulate`, `check_recipient`, `review`), picked by Jev.
  *Done when:* "can I afford that?" in an open session returns the same draft with `objective: "assess_affordability"`, and `/v3` renders it from the real endpoint.
- [ ] **W-13** · Tue · Follow-up turns: a new `transcript` in an open session amends the current draft ("make it $300 from savings"); `simulate` returns code-computed `scenario` values that never enter the draft.
  *Done when:* the transfer and multi-task journeys in `web/src/lib/v3-state.ts` run against the real endpoint.

### Jayanth: validation and transactions

- [ ] **J-01** · Sat · `data/bank.json`: `u_demo` with Savings (SGD), Current (SGD), Invest (USD); payees John Doe, John Smith, Tan Mei Ling, Mum; a credit card biller with amount due; 5 tickers with prices; KYC complete.
  *Done when:* the file loads and Winston confirms it covers the demo commands.
- [ ] **J-02** · Sat · `readonly.py`, `/api/accounts`, `/api/quote`.
  *Done when:* both endpoints return real data from `bank.json` in the CONTRACTS shape.
- [ ] **J-03** · Sun · `app/audit.py`: append-only log; each entry stores the previous entry's hash; a `verify()` function; `/api/audit`.
  *Done when:* a test edits one entry and `chain_valid` turns false.
- [ ] **J-04** · Sun · `devices.py`, `/api/device/register`.
  *Done when:* a test registers a key and reads it back.
- [ ] **J-05** · Sun · `validator.py`, `/api/validate`: LLM compares each field with the transcript and returns per-field verdicts as JSON; on a match, issue an HMAC receipt.
  *Done when:* tests cover a match, an amount mismatch and a payee mismatch, using the fake LLM.
- [ ] **J-06** · Mon · `gateway.py`: `/api/challenge` (check receipt, issue nonce, store payload) and `/api/execute` (nonce exists, unused, unexpired; signature valid).
  *Done when:* tests reject a missing receipt, bad signature, reused nonce and expired nonce, and accept a good one.
- [ ] **J-07** · Mon · `rules.py`: per-transfer limit, daily limit, velocity, KYC, extra-check threshold with OTP (values in D-007).
  *Done when:* demo command 4 returns `needs_extra_check`, and the right OTP executes it.
- [ ] **J-08** · Mon · `ledger.py`: apply transfers, bills and stock orders; state saved in `var/`.
  *Done when:* balances and holdings change correctly, and only `gateway.py` imports `ledger.py`.

### Darren: user interface

- [ ] **D-01** · Sat · Choose the front-end stack. Record it in D-004, list `web/` files in `docs/STRUCTURE.md` §2, and add front-end rules to `docs/STYLE.md` §3.
  *Done when:* those three edits are merged.
  *Local status (10 Oct):* setup docs and frontend prepared on `darren`; merge pending.
- [x] **D-02** · Sun · App shell: mic button and a text box fallback; calls `/api/transcribe` then `/api/understand`.
  *Done when:* typed and spoken input both reach the stub and show its response.
  *Verified (10 Oct):* Playwright typed request and synthetic microphone recording reach the actual API stubs. Real ASR audio compatibility remains D-006.
- [x] **D-03** · Sun · Question view: question text, one chip per option, "None of these"; sends the answer back.
  *Done when:* the stub question renders and the answer call is sent.
  *Verified (10 Oct):* John selection produces the stub draft; "None of these" cancels.
- [x] **D-04** · Sun–Mon · Confirmation window: one card per action, as in CONTRACTS §5, with a live quote for stock orders.
  *Done when:* the transfer, bill and stock-order stub drafts all render correctly on a phone-sized screen.
  *Verified (10 Oct):* three action cards and multi-action examples render; layouts checked at 320/768/1024/1440px. A test stock draft loads the actual quote stub; mismatched ticker quotes are rejected. Signing remains D-05.
- [ ] **D-05** · Mon · Signing flow: device key, register, `/api/validate` → `/api/challenge` → sign → `/api/execute`.
  *Done when:* the full flow runs against stubs, and the private key cannot be exported.
- [ ] **D-06** · Mon · All other states: blocked, unsupported, frozen, needs extra check, executed, rejected.
  *Done when:* each state can be shown by pointing the UI at a stub that returns it.
- [ ] **D-07** · Mon · Audit viewer page using `/api/audit`.
  *Done when:* entries list and `chain_valid` show.
- [ ] **D-08** · Mon · First deployment of the stub version to Tencent Cloud (D-008).
  *Done when:* the public URL works on a phone over mobile data.

## Phase 2: Integration (Tue 13 Oct)

- [ ] **I-01** · All · Demo command 1 works end to end on localhost with real Jev, LLM and signing.
  *Done when:* money moves in the ledger and the audit chain is valid.
- [ ] **I-02** · All · Demo commands 2 to 6 work end to end.
  *Done when:* each gives the result in BRIEF §5.
- [ ] **I-03** · Darren · The deployed version runs command 1.
  *Done when:* it works from a phone over mobile data.

## Phase 3: Hardening (Wed 14 Oct)

- [ ] **H-01** · Winston · Run `scripts/eval_intent.py` on the test set: wrong drafts, questions asked, delay, for single Jev vs Jev × K.
  *Done when:* the results table is in D-003.
- [ ] **H-02** · Jayanth · Record the attack demos: unsigned execute, edited audit entry, mismatch freeze.
  *Done when:* the clips are shared in the group chat.
- [ ] **H-03** · Darren · UI polish and phone layout check.
  *Done when:* all six demo commands look right on a phone.
- [ ] **H-04** · Everyone · Record a 1-minute demo of your own part for the video.
  *Done when:* the clips are shared.
- [ ] **H-05** · All · **Feature freeze at 23:59.** After this, bug fixes only.

## Phase 4: Submission assets (Thu 15 Oct)

- [ ] **P-01** · Winston · Final architecture PPT matches the code.
- [ ] **P-02** · Darren · Video 5–8 min: overview, features, reflection on build approach and CodeBuddy tips.
- [ ] **P-03** · Darren · Cover image 380 × 216 px.
- [ ] **P-04** · Darren · One PDF of all CodeBuddy screenshots, at least 3, under 10 MB.
- [ ] **P-05** · All · Description: Winston (architecture + prompts), Jayanth (safety + business value), Darren (overview + pain points + final edit).
- [ ] **P-06** · Winston · Repo public; README run steps tested on a fresh clone.

## Phase 5: Submit (Fri 16 Oct)

- [ ] **P-07** · All · Check: live link works in a private window; video link viewable without login; GitHub link public; PDF opens. Submit early.
