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
2026-10-10 · Winston · Status: decided (12 Oct): no fallback needed
**Decision:** If no real Jev call works by Saturday 23:00, use an LLM with structured output (forced choice from the same option lists) behind the same `jev.py` interface.
**Why:** Nobody else should be blocked by Jev's early-access status.
**Outcome (12 Oct):** Jev works through the EdgeOne Makers gateway (native System One API, model `@makers/jev`, key `MAKERS_MODELS_KEY`); Darren's live calls returned `jev-1.13.0` (D-019, D-022). `app/intent/jev.py` uses the same gateway settings in `app/config.py`. `TYPESAFE_API_KEY` stays unused unless the gateway fails.

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

## D-022 - V3 component-level presentation
2026-10-11 - Darren - Status: implemented and live JEV verified

**Architecture before:** V2 consumes complete mock Winston output and asks one native System One batch for a bundle, layout, density and optional task priority. Code expands fixed bundles. It retains edits by draft/action identity and computes exact local projections. Real Winston, verified records and signing remain pending; no frontend conversation parsing is appropriate.

**V3:** Add `/v3` and `/api/presentation-v3`. Winston's objective gates a shared functional component catalog. JEV selects primary component, independent optional component visibility, layout, density, interaction and preserve/recompose preference in one request. Deterministic validation restores required editors, projection/scenario dependencies and review. Stable component keys preserve input identity. No names, values, IDs, transcript or financial indicators enter JEV or the developer inspector. Banking contracts remain unchanged.

**Scenario boundary:** Hypothetical edits are separate from the draft and cannot reach review. Only an explicit use-scenario action or updated complete Winston handoff changes committed fields. All changes invalidate approvals and stale model results. Mock conversation turns simulate upstream resolved handoffs; no language interpreter or Claude is added.

**Design:** Existing installed Emil design-engineering skill guides sub-300ms reflow and entry transitions, stable elements, no animated financial counting and reduced-motion support. Reuse Motion and existing button controls. Scope all new tokens/styles to V3. Keep copy short, maintain explicit demo status and estimate limits. Figma integration availability checked; Magic UI unavailable. No new package required.

**V3 verification:** All 11 browser journeys pass (9 original/V2 and 2 V3), including 320/390/768px overflow checks, isolated hypothetical values, explicit adoption, upstream amendments, limit orders, cancelled/edit-stale responses, malformed replies, fallback, per-task review and inspector privacy. Python V3 protocol tests: 11 pass. Desktop/mobile screenshots were visually inspected; one refinement packs supporting components into unused grid cells and shows scenario context side by side on wide screens.

**Tools actually used:** Existing Emil design-engineering skill and Motion for React; existing Coss/Base UI button, Lucide icons, scoped CSS, Playwright with Chrome. Figma MCP created reusable color/spacing/type/motion variables in [DCTA V3 foundations](https://www.figma.com/design/d6KIxLIqQwHCELclIR2fN4). This is a token reference, not a full Figma screen design. Magic UI is not exposed. No package or skill was newly installed because the needed Motion guidance and runtime were already available.

**Live evidence:** Seven sequential browser calls to the real V3 API returned HTTP 200, `jev-1.13.0`, validated source `jev`. Transfer primary: editor, projection, simulator, editor, review. Multi-task primary: summary, then projection. Gateway timings ranged from 1,120 to 2,739ms in this small sample. These are observed timings, not a performance guarantee. Root workspace `JEV-v3-captures.json` stores request contexts, checked API responses and actual frontend manifests; no credentials or banking values are logged there. Live runs are explicit and separate from offline automated tests.

**Limits:** The complete handoffs and banking records are mock, with no live conversation parser in V3. History is unavailable; account comparison remains gated off because fixtures have no same-currency funding alternatives. Real Winston task identity/versioning and backend signing need team integration. State persists across UI recomposition, not page reload. Hypothetical projections exclude fees/taxes/FX/other commitments. JEV is never an affordability approval or authorization system. The endpoint remains a local prototype without production authentication or quotas. Existing V2 behavior and shared banking contracts remain intact; preceding V2 copy edits are separate work.

**Final checks:** `npm run check` passed (TypeScript, ESLint, Prettier, 17 frontend unit tests). `npm run build` passed with static exports for `/`, `/v2` and `/v3`. `npm run test:browser` runs all 11 browser tests. `python -m pytest web/tests/test_presentation.py -q` passes 11 offline protocol tests; Ruff checks/format pass. No tests call the live model.


## D-023: Curate V3 support and animate selection (Darren, 12 October 2026)

Keep one JEV request per composition. Send a categorical viewport hint; offer projection versus simulator emphasis for simulation and projection versus cash-flow emphasis for affordability. Required content remains mandatory. Component-specific criteria describe redundancy and purpose. Rank included optional support by inclusion probability and cap it at two panels on desktop, one on mobile, excluding required nodes and the primary. Preserve eligible layout/density when their probability margin is under 0.15; this is a UI stability heuristic, not calibrated confidence. Keep the prior eligible surface while recomposing.

Use existing Motion for staggered entry, exit, position changes and a shared primary marker. Stable node keys preserve fields; reduced-motion preferences disable spatial transitions. Distinct projection/simulation surfaces and quieter supporting panels establish visual hierarchy. No banking contract, financial calculation or Winston responsibility changes.

**Verification:** TypeScript, ESLint, Prettier, 19 frontend unit tests, all 12 browser tests and 12 Python presentation tests pass. The three V3 browser tests were rerun after fixing reduced-motion overlap, including input identity, mobile panel separation and viewport context. Ruff and production build pass. Desktop/mobile screenshots inspected. Browser replies are mocked; no new live JEV evaluation was performed for this revision. Default root pytest discovery contains no runnable tests; presentation tests were invoked explicitly.

## D-024 · Contracts v0.3: objectives and follow-up turns
2026-10-12 · Winston · Status: decided (Darren adapts the front end)
**Decision:** Every `draft` reply carries `objective` (`prepare`, `assess_affordability`, `simulate`, `check_recipient`, `review`) and `scenario`. A draft no longer ends the session: a new transcript in the same session is a follow-up turn. Jev picks the objective and, per current action, a new value or "keep", from options code built (`build_followup` in `app/intent/options.py`). Code applies the changes and appends the turn to the draft's transcript; the `draft_id` stays. `simulate` puts new amounts and quantities into `scenario` and never changes the draft. `new_task` starts over.
**Why:** Darren's `/v3` (D-021, D-022) expects Winston to resolve the objective and amendments; his mock handoff `{draft, objective, scenario}` maps one to one onto this reply. Keeping every turn in the transcript lets the validation agent check an amended draft against everything the user said.
**Gate:** An unsure objective on a first command becomes `prepare` (it only changes the layout). In a follow-up it is asked, because it decides whether values change. Amendment picks pass the same gate as first commands.
**Front end impact:** Darren's response schemas are strict, so `objective` and `scenario` must be added to his draft-reply schema when this reaches `main`. V1 keeps working by starting a new session per command.
**Limits:** Follow-ups can change amounts, quantities, funding accounts and recipients. They cannot add or remove actions (say the new command instead). One action per type per command still applies.

## D-025 · The input shield rides in the main Jev call
2026-10-12 · Winston · Status: decided
**Decision:** The shield's injection question is asked inside the one Jev batch on the original transcript, not in a call of its own. If Jev flags an injection, nothing else from that batch is used. Known attack phrases ("ignore previous instructions", "you are now", …) are blocked before any Jev call. When the rewriter arrives (W-04), the K rewrites are asked only after that first batch clears the shield, so injected text still never reaches an LLM.
**Why:** The EdgeOne gateway returned HTTP 429 after a few dozen quick calls, and the key is shared with Darren's presentation demo. One call per command instead of two halves the load and cut live latency from about 2 s to 0.8–1.4 s.
**Rejected:** A separate shield call first (doubles calls and latency for the same answer).

## D-026 · Jev never sees real amounts
2026-10-12 · Winston · Status: decided
**Decision:** Every number the user says is swapped for a stand-in before any text reaches Jev (`options.mask`). The stand-in is an HMAC-SHA256 of the value and the scope (user and session) under `MASK_KEY`, shown as two digits then zeros with the real value's digit count (50,000 → e.g. 72,000). Number options read 'The number said as "72000"'; relative amounts name the fraction ("half of the Savings …1234 balance") without the value; follow-up context leaves out amounts and quantities. Jev still answers with option IDs, and code keeps the real values in `Options.numbers`, so nothing is ever decrypted.
**Why:** Amounts are sensitive, and Jev only needs each number's role (amount, quantity, limit price), not its value. It also closes a leak where "all" or "half" sent every account balance to Jev. The hash is keyed so nobody can work a value back by hashing every amount, and scoped so a number keeps its stand-in for a conversation but differs across conversations.
**Rejected:** Markers such as `[N0]` and random 3-digit stand-ins: live, demo commands 1, 2 and 4 dropped below tau ("send [N0] to John" read less like a payment; "send 437" or "send 48213" read less like a large transfer than "send 72000"). Format-preserving encryption: nothing needs decrypting, and it would add a dependency.
**Limits:** Jev learns each amount's order of magnitude. Payee, biller and company names still reach Jev, because it needs them to match; swapping name words the same way is a possible next step. Tencent ASR still hears the audio, and the validation agent still reads the raw transcript, because it checks the draft against the words said. Number words `parse_numbers` doesn't know (such as "dozen") pass through. Previews have no session, so their stand-ins are shared until restart. The rewriter (W-04) must rewrite the masked text and keep its stand-ins unchanged.

## D-027 · Contracts v0.4 (proposed): spoken answers, orders by sum, "the rest", currency conversion
2026-10-13 · Winston · Status: decided (Darren and Jayanth agreed 13 Oct; intent side built, bank side pending)
**Decision:** Four changes, written out in `docs/CONTRACTS.md` §10.
1. A transcript sent while a clarify question is open answers it. Jev picks an offered option, "none of these", or "something different".
2. "Invest $2,000 in Apple" becomes a `stock_order`. Code converts the sum to whole shares at the quote price, rounding down, so the shape is unchanged.
3. "The rest" means what is left after the earlier actions in the same command.
4. A new `fx_conversion` action and `GET /api/fx` (mock rates), so "send 500 to John and invest the rest in Apple" can fund USD shares from SGD savings. The gateway rejects a stale rate with `rate_changed`.
**Why:** In the CLI, a spoken reply to "Which John?" ("John Smith") restarted the conversation, which is wrong for a voice-first app. "Send X and invest the rest" is a natural compound command, but today "the rest" means the whole balance, share orders only take counts, and nothing converts SGD to USD.
**Rejected:** A `budget` field on `stock_order`. Code converts the sum to a quantity, so the card and the signed shape stay as they are. Refusing cross-currency "rest" instead of converting would have broken the headline example.
**Currency of a sum (decided 13 Oct):** any amount said is in SGD unless the user names another currency ("2k" and "$2,000" are SGD; "US$2,000" is USD). An SGD sum for a USD share adds an `fx_conversion`.
**Open:** Validation must re-derive amounts that are computed rather than said (§10.7).

## D-028 · Stage 1 device public-key registration
2026-10-12 · Jayanth · Status: decided (shared documentation for team review)
**Decision:** Store only validated EC/P-256 public coordinates in `var/devices.json`. Pin the first key per known demo user; identical repeats succeed and different keys are refused. Thread/process locks, atomic replacement and disk synchronization protect local persistent storage. No dependencies or public contracts change.
**Limitation:** The client supplies `user_id` without authenticated customer identity or proof of private-key possession. An attacker registering first can take over that user's key slot. This is controlled demo enrollment only, not production authentication; do not expose it as a public banking enrollment feature. Use a supervised private instance for the demo.
**Later:** Authenticated sessions, step-up verification, proof of possession and secure replacement/recovery need a separate design and team approval after the core pipeline works. No automatic reset or key replacement is provided now.
**Errors:** Invalid keys and replacement attempts use HTTP 400 `bad_request`; unknown users use HTTP 404 `not_found`. Corrupt or unavailable registry storage fails with a sanitized server error and is never silently reset.
**Why:** J-06 needs a persistent public key while advanced customer enrollment remains out of scope for J-04. Pinning prevents silent replacement but does not secure the initial enrollment.

## D-029: Isolated V4 with the upstream batch composer (Darren, 12 October 2026)

Preserve V3 and the unfinished-work stash. Add `/v4` with a complete typed handoff from Winston, external draft/scenario state, and the actual json-render experimental composer. Vendor its nine-file core dependency closure unchanged at `fc2a696a50a30cb30c878ab1eb65e102487eea0f` under Apache-2.0, with license and provenance. No package dependencies added. Sequential editing is deferred because the prior live experiment changed a root variant without supplying the required affordability content.

Use batch selection followed by parent/slot/order evaluation: two sequential JEV calls per fresh composition. A shared JSON catalog defines approved components, variants, bindings and eligibility. Python rebuilds server-owned instructions and validates client question IDs; it narrows mandatory choices and semantic placements. The frontend checks the actual generated tree for schema validity, unique resources, complete task coverage, objective content, semantic parents and optional-panel limits. A rejected or unavailable composition gets a complete labelled local fallback. Values and transcripts never enter this evaluator.

Keep the DCTA renderer: stable flat React keys preserve input DOM nodes even when the logical tree changes parents. The experimental upstream React renderer remounted controls during the earlier reparenting test. Draft and hypothetical values remain separate until explicit adoption. Review requires every task acknowledgement and produces only a local demo completion. Winston still owns interpretation, routing and clarification; this route supplies mock resolved handoffs and does not implement his conversation logic or banking execution.

Figma MCP created editable desktop and mobile references with shared color variables: [DCTA V4](https://www.figma.com/design/M6ldC5Ew9cGEBrKxR8T8aC). Applied frontend engineering and Emil motion guidance: concise task surfaces, green projections, explicit review, 150–240ms entry/exit/position motion, reduced-motion support, focus/caret retention and immediate financial recalculation. Desktop and mobile screenshots were inspected; fixes removed inherited page chrome and mobile rail overflow. Cached layouts are marked in the inspector; edits alone do not call JEV.

Verification: 25 frontend unit tests, 16 Python protocol tests, 14 Chrome browser tests (including existing versions), TypeScript, ESLint, Ruff and static production build pass. V4 formatting passes. The full Prettier check still reports 12 unchanged baseline V3/related files; they were deliberately left intact. Default root pytest discovery finds no tests, so `web/tests` was invoked explicitly. Seven fresh live compositions returned 14 HTTP 200 responses; a further amendment reused the cached preparation layout. All eight displayed compositions passed frontend checks. Full evidence is in the root workspace `JEV-v4-report.md` and `JEV-v4-captures.json`; checked replies are captured, raw gateway probability vectors are not.

Limits: prototype endpoint without production authentication or quotas; real Winston transport and banking signing remain team integration work. State survives recomposition but not reload. Layout caching is categorical and per session. Financial estimates exclude fees, taxes, FX and other commitments. The Figma file is a design reference rather than a full interactive prototype.


## D-030 · Optional V4 financial context and data-bound diagrams (2026-10-12)

Add optional `data.financial` to the V4 handoff, without changing shared banking contracts. Each account entry inherits currency from its referenced bank account. It contains a dated commitments window, optional user-confirmed reserve and historical spending categories with an explicit period. Missing fields mean unknown; empty supplied bill lists mean no bills in that window. Calculations use exact cents. An explicitly linked draft bill reduces that obligation's unpaid remainder; historical spending is never subtracted. Providers must supply outstanding commitments not already deducted from the input balance and establish reliable draft links.

JEV receives categorical funding, commitments, reserve and context-availability signals. AccountInsights offers compact/expanded variants with a bill timeline and accessible spending bars. BalanceProjection adds a funding-gap SVG, eligible only with a shortfall. Missing-estimate, shortfall and reserve-breach notices stay outside optional model-selected content. Existing motion and reduced-motion behavior apply. The fallback includes compact insights when context exists.

Direct edits recalculate figures and notices locally. The next objective change or explicit Recompose computes fresh context and uses its categorical cache key. Edits do not request a new layout on every keystroke. Fixtures add synthetic October bills, a reserve and September spending. Winston still owns resolution/routing; bank integration remains separate. Use data-bound diagrams instead of decorative raster images to keep the task concise.

## D-031 — V5 orb entry and adaptive task surface (2026-10-13)

Darren approved V5 as a separate route, preserving V4. Port the supplied Orb.dc.html visual direction into native React: dark shell, red orb, white task surfaces, concise typography and purposeful motion. Orb activation reveals explicit demo request buttons. No microphone is recorded. Clarification and shield results are labelled mock Winston responses and never delegated to JEV. Resolved handoffs pass the existing runtime validator before V4 selection/placement composition; no banking contracts or backend routes change.

Reuse the V4 component registry, editable state and exact financial calculations. Each explicit composition requests the existing two sequential JEV stages; edits recalculate locally. Pending work can be cancelled; stale replies cannot restore an abandoned workspace. Fallbacks are labelled. Review acknowledges every action and completes only a local demo. Future Winston integration replaces the fixture producer with a resolved handoff, while real validation/signing remains separate integration work. The reference's unsupported second-check and sent claims are omitted.

Adaptive layout uses a conversation column on desktop and stacked content on mobile, with natural scrolling instead of the export's fixed artboard. Motion uses the installed motion package, a shared orb/surface transition and reduced-motion support. No new dependency or exported support.js/React runtime is added.

V5 verification: production static build, TypeScript, targeted ESLint, 28 frontend unit tests, 17 Python protocol tests, Ruff and two V5 Chrome tests passed. Browser tests cover both motion preferences, all four demo choices, two-stage composition using deterministic replies, edits, simulation, explicit review, malformed replies, reset cancellation and 320/390/768/1440px overflow. Desktop workspace and mobile choice screenshots were visually inspected. Live gateway quality and real Winston transport were not tested in this change.

## D-032 - V5 concise confirmation cards and bank API action (2026-10-13)

Darren requested replacing developer-oriented V4 forms in V5 with the compact reference design. V5 now renders read-only confirmation facts from the resolved draft, with a prominent amount and concise recipient/biller, account and stock details required by CONTRACTS section 5. Optional account context is collapsed. Affordability adds a small data-bound chart without unrelated tasks. No review objective, acknowledgement checkboxes, composition inspector or recompose button is exposed in V5. V4 remains unchanged.

The explicit Transfer now / Pay now / Place order button confirms the displayed draft once, then calls validate, device/register, challenge and execute. Device keys are non-extractable P-256 private keys persisted in IndexedDB; registration is serialized across tabs. Only the exact server signing_payload is signed, after checking its draft and nonce match and its expiry. An OTP field appears only on needs_extra_check and reuses the nonce/signature. Mismatch, rejection and network uncertainty have explicit user outcomes; unknown execution outcomes disable retries. No transaction runs on page load, during composition, or while inspecting affordability.

This supersedes V5's local-only completion in D-031 at Darren's explicit request to connect the bank API. Prototype request buttons still produce mock resolved handoffs; their transcript and date match the displayed request. There is no local edit-and-sign path: corrections must come through Winston's future handoff. No shared banking contract or bank implementation changed. Current bank routes are stubs, including an expired placeholder challenge, so they cannot complete this checked signing path. Do not claim end-to-end real execution until Jayanth supplies valid responses and Winston supplies resolved drafts.

Verification: 29 frontend unit tests, TypeScript, targeted ESLint, production static build and four Chrome tests passed. Browser checks cover compact card layout at 320/390/768/1440px, absence of review tasks, exact WebCrypto signature verification, OTP reuse, mismatch/expiry stops, bank rejection, uncertain execution, multi-action completeness and duplicate-click protection. API replies were controlled test responses; no real money was moved. Mobile transfer and desktop/affordability screenshots were inspected. Signing cannot be fully exercised against the current placeholder bank challenge.

## D-033 · First integration pass: V5 on the real engine and bank data
2026-10-13 · Winston · Status: decided (Winston's choices; Darren and Jayanth review their areas)
**Decision:** Integrate the three branches on `winston` before any further PR. Each choice below was made by Winston; the code in others' areas was written by his session, committed under their area prefixes for review.
- **Bank (Jayanth):** `readonly.py` gains `list_quotes`, `get_bill_reference` and `get_fx_rate` (CONTRACTS §3.3). `data/bank.json` gains `fx_rates` (SGD→USD 0.740000, USD→SGD 1.351351, the contract's example values) and `GET /api/fx` serves them. Validation, challenge, execute, rules and ledger stay stubs until J-05 to J-08.
- **UI contracts (Darren):** `web/src/lib/contracts.ts` moves to v0.4: draft replies carry `objective` and `scenario`; `fx_conversion` joins the action union. `/`, V4 and V5 show it as a read-only Convert currency card (CONTRACTS §5 order); balance projections credit the account it pays into. V2 and V3 refuse drafts with a conversion.
- **V5 (Darren):** typed requests and the demo shortcuts go to `/api/understand`; questions, blocked, unsupported and cancelled replies are the engine's own. The handoff is the draft plus `/api/accounts` and `/api/quote` (`source: "supplied"`). "Can I afford it?" is a follow-up turn in the same session. `check_recipient` maps to `prepare`, which carries RecipientDetails in the V4 catalog. Real drafts carry no financial context: bars show real balances and "unlisted bills" until a cashflow endpoint exists.
- **Serving:** FastAPI mounts `web/out/` after the API routes; unknown `/api/` paths keep the JSON 404.
- **Intent (Winston):** follow-up options leave out the current account and payee, and the gate counts "default" and the account it resolves to as one pick. Both fixes come from live runs on the real seed, where Jev split its probability between two options that build the same draft.
**Why:** The pieces were built against stubs and fixtures; nothing joined them. Integrating on one branch first shows every gap at once.
**Rejected:** Leaving each area to integrate itself (the gaps sit between areas); building J-05 to J-08 here (Jayanth may be mid-way through them); mock bills on real drafts (mixes mock and real data on one screen).

## D-034 · Contracts v0.5: document the presentation endpoints
2026-10-14 · Winston · Status: decided (records endpoints already in use; Darren reviews §3.4)
**Decision:** CONTRACTS §3.4 documents `POST /api/presentation-v4/evaluate` (V4, V5) and lists `/api/presentation-demo` (V2) and `/api/presentation-v3` (V3). Their models stay in `web/` with Darren. A missing model key returns 503 `upstream_error`.
**Why:** V5 calls Jev twice per screen through these endpoints, on the same key as the intent engine, but only Darren's decision entries (D-019, D-021, D-029) described them. Changes to them should go through the same agreement as every other endpoint.
**Rejected:** Leaving them undocumented (a change to them needed no one's agreement).

## D-035 · Contracts v0.6 (proposed): read-only answers, starting with balances
2026-10-14 · Winston · Status: open (Darren and Jayanth to agree)
**Decision:** `/api/understand` gains `status: "answer"` for questions that move no money. The first `kind` is `balance`: Jev says a balance is asked for and which account (or all); code reads the figures from `readonly.get_accounts`. An answer is never validated or signed and ends the session. A command that also moves money gets the draft instead.
**Why:** "What's my balance?" is the most basic voice-banking request and today gets "unsupported". A separate status keeps read-only replies out of the confirm-and-sign flow, and `kind` leaves room for more read-only questions (transactions, bills, holdings) without another status.
**Rejected:** A draft with a "balance" action (it would show a Confirm & sign button for something that moves nothing); answering in free LLM text (Jev never writes values, AGENTS §4).
**Update (2026-10-14):** v0.6 now proposes eight read-only kinds (`balance`, `transactions`, `bills`, `holdings`, `quote`, `fx_rate`, `payees`, `help`), chosen by one Jev question (`info`) instead of a yes/no question per kind. A transactions question with no period returns the latest 5.

## D-036 · Contracts v0.7 (proposed): moving money between your own accounts
2026-10-14 · Winston · Status: open (Darren and Jayanth to agree)
**Decision:** New action `account_transfer` for moves between the user's own accounts in one currency, shown as a **Move money** card. A move between currencies is a standalone `fx_conversion`, which also makes "convert 1,000 SGD to USD" work. Limits and the extra check for own-account moves are Jayanth's call under D-007.
**Why:** Moving money between your own accounts is one of the most common banking requests, and `transfer` only pays payees. `fx_conversion` already describes a move between two own accounts, so reusing it avoids a second shape for the cross-currency case.
**Rejected:** Treating own accounts as payees (they would need payee IDs, and a payee is someone else); same-currency only (leaves "move 1,000 from savings to invest" unsupported).
