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
2026-10-10 · Darren · Status: decided (base); `web/` file list due Sun 11 Oct (D-01)
**Decision:** `web/` is based on shapeshift (github.com/anishfn/shapeshift): Next.js (App Router), React 19, TypeScript, Tailwind CSS v4, shadcn/ui, Motion, built with Bun. It is built as a static export (`output: "export"`) and FastAPI serves the built folder. Darren lists the `web/` files in `docs/STRUCTURE.md` §2, adds the build folders (`web/out/`, `web/.next/`) to `.gitignore`, and adds front-end rules to `docs/STYLE.md` §3.
**Remove from shapeshift:** `src/app/api/intent/route.ts`, `src/lib/jev/client.ts` and `src/lib/jev/mock.ts`. Jev is called only by `app/intent/jev.py` (AGENTS §4), and a keyword guess must never stand in for Jev on a money command.
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

## D-015 · Contracts v0.2: shapeshift-style live preview, server-side gate
2026-10-10 · Winston · Status: decided (merged by Winston without the usual three-way review; Darren and Jayanth check it at the next sync)
**Decision:** Fit the AI-side contract to shapeshift's interaction model (CONTRACTS v0.2):
- New `POST /api/preview`: debounced as the user types, one Jev call on the raw text, returns shapeshift's `intent` shape (`value`, `confidence`, `probabilities`) over `transfer`, `bill_payment`, `stock_order`, `none`, so its `decide.ts` drives the morphing card. Display only: no values, no drafts.
- `/api/understand` stays the only way to a draft. The server's gate decides `question` vs `draft`; the UI obeys `status` and applies no thresholds of its own to it.
- Every intent reply carries `meta` (latency, model, source, phrasings, cached) for the latency readout and H-01. Questions carry `evidence` for a debug view. `evidence` gains per-option `probabilities`.
- The UI creates `session_id`; sessions expire after 5 minutes; 3 questions at most, then `cancelled` with `too_many_questions`.
- Timing is event-driven (CONTRACTS §9): one call per user action, no polling except the stock price every 10 s while a stock card is open. Cancel aborts the call in flight.
- Intent audit events are listed in CONTRACTS §8, written through `app/audit.py` `append(actor, event, data)`.
**Why:** Darren is building on shapeshift, so matching its result shape and request loop saves him rewriting it. Keeping the gate on the server keeps safety promise 2 ("No guessing") true even if the UI is buggy or tampered with.
**Rejected:** The UI deciding from probabilities, as shapeshift's `decide.ts` does for its cards (a UI bug could skip a question on a money command); polling `/api/understand` on an interval (nothing changes between user actions); streaming ASR for live voice text (too much setup; the browser's built-in recognition may feed `/api/preview` instead).

## D-016 · Adaptive task pages: first frontend slice
2026-10-10 · Darren · Status: decided
**Decision:** Build predefined transfer, bill and stock React cards from contracts v0.2, with read-only related account/holdings information and mock quotes. Corrections restart intent processing. Explicit example buttons load labelled fixtures for UI exploration only. The first slice ends at validation; signing remains D-05. Use npm locally because Bun is unavailable, retaining Bun-compatible scripts and a checked-in npm lockfile. Use Zod for response checks, Motion for transitions, native accessible controls for the initial cards, and Playwright for browser verification. Shadcn components can be adopted when richer controls are needed.
**Why:** Darren can verify the adaptive experience against the existing stubs without changing the transaction contract. No client classifier or generated executable UI is needed. Prettier keeps the frontend source formatted.
**Open integration:** agree audio encoding in D-006 (the initial browser recorder sends its supported format to the stub), structured correction API, multi-action failure semantics, and FastAPI mounting of web/out before deployment.


## D-017 - Adaptive financial workspace and presentation handoff
2026-10-10 - Darren - Status: decided (frontend); live JEV handoff open
**Decision:** Use a strict, versioned frontend UI manifest and deterministic layout/component registry. Eligible layouts are focus, split, comparison, timeline, sequence and canvas. Choose from structured intent, action count, available account context and scheduling evidence, never exact prompt strings. Every action appears exactly once; review controls exist outside model-controlled composition. The selection adapter accepts only ranked layout enums, bounded scores, a primary action index and approved support enums. No JSX, scripts, URLs or bank operations are accepted.
**Editing:** Keep raw user edits and per-action approvals in page state, outside the renderer. Validate amounts as decimal strings and share quantities as safe positive integers. Calculate projections with integer cents per account/currency; do not invent exchange rates, fees or schedules. Recomposition preserves edits. An edit invalidates that action's approval and the plan's validation. Mock examples can be reviewed locally. API edits cannot submit the original draft or execute; the user must obtain a fresh backend draft until correction/revalidation is agreed.
**Proposed handoff (not a backend contract change):** Winston's server supplies structured workspace intent (transaction/decision), eligible facts and a JEV presentation selection after intent resolution. Selection shape: `{mode, ranks: [{layout, score}], primary, supporting}`. A server adapter must run JEV only against eligible option IDs; the client validates its output with `selectionSchema` then `composeWorkspace`. Do not pass model transaction values through this adapter. Keep credentials server-side. Current endpoints and JEV client are stubs, so the application uses labelled demo/fallback selection, not live JEV. A response extension or separate operation requires all three teammates' agreement before implementation.
**Limitations:** Timeline is gated off because the current contract contains no schedules. Decision intent is available through explicitly selected demos; free text still follows the existing stub's clarification loop. Ownership, funds, limits, recipient verification and signing remain backend responsibilities. No banking execution logic is changed.
**Visual system:** DBS-inspired red/black/white, editorial typography, flat bordered sections, differentiated focus/sequence/canvas composition, restrained Motion transitions and reduced-motion support. Reuse existing shadcn/Coss primitives; add no paid dependencies.

## D-018 - Separate conversational morphing experiment
2026-10-10 - Darren - Status: decided (offline frontend); live integration open
**Decision:** Add `/v2/` on Darren's existing branch and preserve `/`. One reducer owns conversation, interpreting, partial, workspace, refinement, review and demo-result states. Draft values, raw edits and approvals live outside the view. Ambiguous Johns and missing transfer amounts create a partial workspace; numeric conversational amendments preserve unrelated edits. Affordability recomposes the workspace and calculates remaining balances with integer cents, separately by account/currency. Confirming requires explicit review of every action. This route never calls understanding, validation, signing or execution endpoints, and never claims a banking transaction occurred.
**JEV presentation shape:** Strict version-2 choices contain layout (the existing six enums), interaction (`execute`, `clarify`, `compare`, `simulate`, `explore`), focus (`amount`, `recipient`, `affordability`, `deadline`, `risk`), approved supporting components, tools (`form`, `slider`, `comparison`, `chart`, `timeline`), density (`minimal`, `standard`, `detailed`), strategy (`partial`, `focused`, `multi`) and primary action index. Eligibility excludes schedules/timelines/deadlines without verified data. Every complete action remains present; form and review controls cannot be removed. “Execute” is a presentation intent, never bank authorization. The UI uses approved React components, no generated JSX.
**Adapter boundary:** `decidePresentation` accepts an optional server-side transport receiving structural action types and approved option IDs only. There is no invented EdgeOne endpoint, client credential or live request. Response schemas and eligibility are checked before composition; exceptions and invalid choices use deterministic fallback. Debug traces contain eligible choices, selection, manifest and fallback reason, with no transaction values or names. Connecting this transport, verified records, live intent resolution and correction/revalidation needs the team-agreed server handoff. Existing contracts remain unchanged. No Claude processing is added.
**Design:** Use DBS red and a minimal editorial layout, existing shadcn/Coss primitives and installed Motion. UI UX Pro Max and Open Design's design engineering skill were installed globally before UI construction; their accessibility, hierarchy and restrained animation guidance informed the work. Open Design's marketing-focused taste skill was installed but its page recipe is unsuitable for this multi-step banking tool. No new runtime dependency is required.
**Limits:** Numeric English demo parsing is deliberately limited and is not a production intent recognizer. Known fixture balances, bill amounts, stock prices and recipient records are labelled mock. Unsupported recipients/currencies and ambiguous amendment targets require clarification rather than replacing task data. Missing stock quantity is requested in conversation; only transfers currently have a dedicated partial form. Fees, taxes, FX and external commitments are excluded from projections. Voice continues to use the existing transcription stub. Timeline stays gated off until verified schedule data exists.

## D-019 - Live presentation JEV with mock Winston handoff
2026-10-11 - Darren - Status: live local placeholder verified; Winston integration pending
**Decision:** The explicit mock-output selector triggers live presentation composition only after a resolved fixture is selected and the button is clicked. No model calls while typing or directly editing. Load the fixture through a reducer event, not prompt parsing. Recomposition of the same output preserves raw user edits. Stale/cancelled responses cannot replace the presentation. Failed HTTP requests, malformed replies and ineligible choices use local fallback with a visible reason. All task forms and explicit review steps remain outside JEV control.
**Protocol:** Native System One at `https://ai-gateway.edgeone.link/v1/systemone`, model `@makers/jev`, Bearer `MAKERS_MODELS_KEY`. Body uses `model`, structural `state`, and typed `questions` with `choice` criteria maps. Six scalar questions select layout, interaction, focus, density, strategy and primary action; optional support/tool questions select include/omit. The form tool is always present. No transaction values, names, free user prompt, credentials or money-changing operation are sent as state. Check each response choice, confidence and probability map against its offered options; the frontend checks schema and eligibility again. Show actual model identity, answers, probabilities, choices and final manifest in the debug disclosure.
**Isolation approved by Darren:** `web/jev.py` owns the experimental presentation client/router and is registered in shared `app/main.py`; shared `app/config.py` alone reads the new key. This is an explicit presentation-only exception to the single intent-client rule. Winston's `app/intent/jev.py` remains untouched. The experimental route accepts a Literal fixture query argument, not a banking draft or a new shared Pydantic contract. Existing banking endpoints, `app/contracts.py` and CONTRACTS v0.2 are unchanged. No dependency is added. Future real Winston output needs the team-agreed handoff; this does not claim live intent resolution or validated banking records.
**Deployment limit:** Local API proxy already routes `/api/` to FastAPI. Static deployment still requires that server route under the same origin and server-side environment configuration. This placeholder is a local demonstration, not a public production endpoint with authentication or usage limits. Live smoke checks are run explicitly outside the automated test suite; automated browser tests mock the gateway response.


## D-020 - Integrated V2 plan and shared editable pieces
2026-10-11 - Darren - Status: implemented frontend composition
**Decision:** Replace isolated ActionCard shells in V2 with numbered task sections sharing one plan surface. Export ActionFields for reuse by the original cards and V2. Amount/quantity, recipient, funding and order controls preserve the same reducer edits and validation. Each task has a linked estimate and, when projection is selected, its account's remaining balance after all tasks. Shared optional context remains controlled by the existing JEV manifest. Density changes spacing and estimate arrangement; layout and focus control placement and emphasis. Stable action-index keys and reduced-motion-aware position transitions preserve input identity. Original cards and banking contracts remain compatible.
**Limit:** This is a renderer refactor, not a new JEV field-level protocol. JEV still selects the existing approved layout, focus, density, support and tools. It does not independently select individual fields or invent arbitrary component groupings. All mandatory editable fields and the explicit review flow remain code-controlled. Live Winston integration remains pending.


## D-021 - Complete Winston output and coherent presentation bundles
2026-10-11 - Darren - Status: implemented; live Winston integration pending
**Boundary:** Winston owns context breakdown, task routing, objective selection and ambiguity resolution before this UI is created. Remove the V2 command/amendment parser and partial forms; the historical v2-parser.ts filename now contains mock records only. The selector simulates complete handoffs, including objective changes on one transfer. Existing shared banking contracts and intent code remain unchanged; no Claude processing is added.
**Presentation:** One native System One request asks bundle, layout and density, plus visual primary for multiple tasks. Approved bundles: transfer_editing, recipient_check, affordability_overview, affordability_simulation, investment_assessment and multi_task_review. Bundle-to-components mapping is deterministic on server and checked again by the frontend. Affordability always includes projections; simulation includes per-task sliders. Required forms and review remain outside model control. Layout affects visual order only, never draft execution order. Recipient status is listed, never invented bank verification. Account comparison is not offered without real alternative funding context.
**Experimental handoff:** POST /api/presentation-demo accepts a strict structural body: action_types, complete true, missing empty, Winston-supplied objective, recipient_status, available_context and optional current_ui (bundle/layout/density). Unknown fields and incomplete/inconsistent context are rejected. No names, amounts, IDs or transcript cross this boundary. These are explicitly mocked facts, not an agreed production Winston contract or verified live records.
**Continuity:** Same objective plus a probability margin below 0.15 retains an eligible current bundle or layout. This is an experimental preference-stability policy, not calibrated UI quality or banking approval. Objective changes may recompose. Same-draft same-index same-task identity retains only edits to fields unchanged upstream. Upstream changes win; new draft IDs reset edits; every handoff clears approvals. Real Winston integration needs an agreed stable task identity/version policy. Generation guards abort or ignore cancelled and edit-stale replies.
**Verification:** Frontend unit and browser checks cover incomplete-output rejection, coherent bundle dependencies, objective transitions, stable layouts, edit reconciliation, explicit review, fallback and cancellation. Live local smoke checks for transfer editing, affordability and recipient checking returned HTTP 200 using jev-1.13.0. Python pytest currently collects no tests; separate inline request/parser and HTTP rejection checks were run. Original page remains covered by browser tests.
