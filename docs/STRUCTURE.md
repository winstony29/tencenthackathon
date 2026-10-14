# Structure

**Purpose:** the complete list of files allowed in this repo. **If a file is not listed here, it must not exist.** To add one, follow `AGENTS.md` §5: ask the human, then add it here in the same commit.
**Owner:** Winston · **Last updated:** 2026-10-10

---

## 1. The tree

```
dcta/
├── AGENTS.md                 rules for all coding assistants                 shared
├── CLAUDE.md                 one line: @AGENTS.md                            shared
├── CODEBUDDY.md              one line: @AGENTS.md                            shared
├── README.md                 how to install and run                          Darren
├── requirements.txt          pinned Python packages                          shared
├── pyproject.toml            ruff + pytest settings only                     shared
├── .env.example              every env variable, no real values              shared
├── .gitignore                                                                shared
│
├── docs/
│   ├── BRIEF.md              what and why                                    Winston
│   ├── ROLES.md              who owns what                                   Winston
│   ├── CONTRACTS.md          data shapes + endpoints                         shared
│   ├── STRUCTURE.md          this file                                       shared
│   ├── STYLE.md              code + docs style                               Winston
│   ├── CHECKLIST.md          build sequence                                  everyone
│   ├── DECISIONS.md          decision log                                    everyone
│   ├── GITHUB.md             git commands for the daily workflow             Winston
│   ├── DCTA_endpoints_and_data.pptx  endpoint map, data formats, timing      Winston
│   └── screenshots/          CodeBuddy proof images only                     everyone
│
├── app/
│   ├── __init__.py
│   ├── main.py               FastAPI app; mounts routers; serves web/        shared (Winston)
│   ├── config.py             reads env vars; all thresholds and constants    shared (Winston)
│   ├── contracts.py          Pydantic models matching CONTRACTS.md           shared (Winston)
│   ├── llm.py                one client for LLM calls (rewriter, validator)  Winston
│   ├── audit.py              append-only hash-chained log                    Jayanth
│   │
│   ├── intent/                                                               Winston
│   │   ├── __init__.py
│   │   ├── router.py         /api/transcribe, /api/understand
│   │   ├── asr.py            Tencent ASR client
│   │   ├── shield.py         injection check (Jev yes/no)
│   │   ├── rewriter.py       LLM writes K plain rewrites
│   │   ├── jev.py            the only file that calls TypeSafe's API
│   │   ├── options.py        builds option lists from transcript + user data
│   │   ├── gate.py           average/spread rule; decides ask or proceed
│   │   ├── draft.py          assembles the draft from Jev's picks
│   │   └── session.py        in-memory state for the clarify loop
│   │
│   ├── validation/                                                           Jayanth
│   │   ├── __init__.py
│   │   ├── router.py         /api/validate
│   │   └── validator.py      LLM audit of draft vs transcript; receipts
│   │
│   └── bank/                                                                 Jayanth
│       ├── __init__.py
│       ├── router.py         /api/accounts, /api/quote, /api/device/register,
│       │                     /api/challenge, /api/execute, /api/audit
│       ├── readonly.py       read-only views of accounts, payees, billers, quotes
│       ├── gateway.py        nonce + signature checks; only caller of ledger
│       ├── rules.py          limits, velocity, KYC, extra-check threshold
│       ├── ledger.py         mock balances and holdings; moves money
│       └── devices.py        stores registered public keys
│
├── data/
│   └── bank.json             demo user, accounts, payees, billers, prices     Jayanth
│
├── web/                      front end; files listed in §2 once D-004 is set   Darren
│
├── tests/
│   ├── conftest.py           shared fixtures (test client, fake Jev/LLM)      shared (Winston)
│   ├── test_boundary.py      enforces the import rules in AGENTS.md §4        Winston
│   ├── test_intent.py                                                         Winston
│   ├── test_validation.py                                                     Jayanth
│   ├── test_bank.py                                                           Jayanth
│   └── commands.jsonl        ~30 test commands with expected results          Winston
│
├── scripts/
│   ├── chat.py               text conversation with /api/understand (live Jev)  Winston
│   └── eval_intent.py        single Jev vs Jev × K comparison report           Winston
│
└── var/                      runtime state (ledger, audit, nonces); git-ignored
```

## 2. `web/` files

The first frontend slice uses the stack in D-004 and the integration choices in D-016. Build output (`web/out/`, `web/.next/`, `web/dist/`) and `web/node_modules/` are never committed.

```
web/
├── jev.py                    presentation-only System One router (mock Winston outputs)
├── package.json              dependencies and checks
├── package-lock.json         npm lockfile (Bun can install package.json)
├── next.config.ts            static export and development API proxy
├── next-env.d.ts             Next.js generated type references
├── tsconfig.json             strict TypeScript settings
├── postcss.config.mjs        Tailwind v4
├── eslint.config.mjs         Next.js lint rules
├── THIRD_PARTY_LICENSES.txt  component attribution and licence notices
├── src/
│   ├── app/
│   │   ├── layout.tsx        document shell
│   │   ├── page.tsx          adaptive banking workspace
│   │   ├── v2/page.tsx       complete Winston-output presentation experiment
│   │   └── globals.css       responsive design tokens and styling
│   ├── components/
│   │   ├── ui/
│   │   │   ├── button.tsx   adapted Coss button with DBS-inspired variants
│   │   │   ├── checkbox.tsx adapted Coss / Base UI checkbox
│   │   │   ├── card.tsx     adapted shadcn card composition
│   │   │   └── textarea.tsx adapted shadcn text area
│   │   ├── v2-workspace.tsx   V2 approved forms and interactive support registry
│   │   ├── workspace-renderer.tsx  approved layout registry and projections
│   │   ├── action-card.tsx   editable transfer, bill and stock review cards
│   │   ├── task-review.tsx   draft review and workflow result states
│   │   ├── context-panel.tsx related balances, holdings and quotes
│   │   └── command-input.tsx typed input and audio recording
│   └── lib/
│       ├── v2-conversation.ts complete handoff reconciliation and explicit review state
│       ├── v2-parser.ts      mock banking records only (historical filename)
│       ├── v2-presentation.ts approved bundles, presentation context and UI stability
│       ├── composition.ts   typed manifest, eligibility and selection adapter
│       ├── draft-editor.ts  immutable edits, validation and projections
│       ├── contracts.ts     runtime schemas matching contracts v0.2
│       ├── api.ts           cancellable JSON requests
│       ├── examples.ts      explicitly selected UI demo fixtures
│       └── money.ts         exact estimated stock totals
└── tests/
    ├── contracts.test.ts    boundary and exact-money checks
    └── browser.test.mjs     browser integration and layout checks
```

## 3. Screenshot naming

`docs/screenshots/<name>_<yyyy-mm-dd>_<n>.png`, for example `winston_2026-10-11_1.png`. Only CodeBuddy/WorkBuddy proof goes here.

## 4. Change log for this file

| Date | Who | Change |
|---|---|---|
| 2026-10-10 | Winston | First version |
| 2026-10-10 | Winston | Add `docs/GITHUB.md` |
| 2026-10-10 | Winston | Add `docs/DCTA_endpoints_and_data.pptx` |
| 2026-10-10 | Darren | Register first frontend slice (D-01 to D-04) |

## V3 presentation experiment (Darren, D-022)

The V3 brief authorizes this separate route and these focused modules. V1 and V2 remain available.

- `web/presentation-catalog.json`: shared approved functional component metadata and dependencies.
- `web/presentation.py`: structural V3 JEV request builder, checked answers and router.
- `web/src/app/v3/page.tsx`: complete mock Winston journeys, live composition and inspector.
- `web/src/app/v3/v3.css`: scoped banking design and motion tokens.
- `web/src/lib/v3-registry.ts`: typed component bindings, eligibility and privacy-minimized context.
- `web/src/lib/v3-presentation.ts`: deterministic manifest validation, fallback and stability.
- `web/src/lib/v3-state.ts`: complete handoffs, isolated scenarios and review state.
- `web/src/components/v3-primitives.tsx`: reusable field, metric and section primitives.
- `web/src/components/v3-editors.tsx`: mandatory task editors and explicit review.
- `web/src/components/v3-analysis.tsx`: projections, scenario controls and supporting context.
- `web/src/components/v3-workspace.tsx`: stable keyed functional registry renderer.
- `web/tests/v3.test.ts`: registry, manifest, state and financial integrity tests.
- `web/tests/v3.browser.test.mjs`: V3 browser journeys, stale responses and responsive checks.
- `web/tests/test_presentation.py`: offline Python request, parser and endpoint tests.


## V4 isolated presentation workspace (Darren, 12 October 2026)

Authorized V4 implementation; V1/V2/V3 remain available.

- `web/v4-catalog.json`: shared approved component recipes and objective guidance.
- `web/presentation_v4.py`: bounded EdgeOne evaluator adapter; presentation only.
- `web/src/app/v4/page.tsx`, `web/src/app/v4/v4.css`: V4 route and scoped design.
- `web/src/lib/v4-state.ts`: typed resolved handoff, drafts, scenarios and review state.
- `web/src/lib/v4-demo.ts`: synthetic Winston journeys and banking fixtures.
- `web/src/lib/v4-catalog.ts`: eligible app-owned recipes and schema.
- `web/src/lib/v4-composition.ts`: upstream composer integration, acceptance and fallback.
- `web/src/components/v4-workspace.tsx`: stable task component rendering and motion.
- `web/src/components/v4-editors.tsx`: bound task editors and review.
- `web/src/components/v4-analysis.tsx`: deterministic projections, scenarios and context.
- `web/src/components/v4-primitives.tsx`: accessible V4 form/metric primitives.
- `web/tests/v4.test.ts`, `web/tests/v4.browser.test.mjs`, `web/tests/test_presentation_v4.py`: V4 validation and interaction tests.
- `web/vendor/json-render/{actions,directives,experimental-compose,experimental-composition-batch,experimental-composition-tree,props,spec-validator,types,visibility}.ts`: unchanged minimal upstream composer dependency closure.
- `web/vendor/json-render/LICENSE`, `web/vendor/json-render/provenance.json`: Apache license and exact source pin.


V4 financial-context additions (Darren):
- `web/src/lib/v4-financial.ts`: optional typed account context, exact-cent commitment and reserve calculations.
- `web/src/components/v4-context.tsx`: mandatory financial notices and optional bill timeline/spending diagrams.


V4 financial-context additions (Darren):
- `web/src/lib/v4-financial.ts`: optional typed account context and exact-cent commitment/reserve calculations.
- `web/src/components/v4-context.tsx`: financial notices and optional bill timeline/spending diagrams.
`web/src/app/v4/components/page.tsx`: interactive preview of real V4 financial components using mock data, without JEV calls.

## V5 orb interface (Darren, 2026-10-13)

- `web/src/app/v5/page.tsx`: orb entry, explicit mock Winston choices, validated handoff and existing JEV orchestration.
- `web/src/app/v5/v5.css`: scoped responsive dark shell and white task surfaces; reduced motion.
- `web/src/components/v5-orb.tsx`: accessible animated orb and shared surface transition.
- `web/tests/v5.browser.test.mjs`: entry gating, clarification, JEV composition, edits, cancellation, review and responsive checks.

### V5 task confirmation and bank connection

- `web/src/components/v5-cards.tsx`: concise action summaries and financial impact charts, with optional account context collapsed.
- `web/src/components/v5-payment.tsx`: single confirmation action, bank result and conditional OTP entry.
- `web/src/lib/v5-bank.ts`: runtime-checked validation, persistent non-extractable device key, challenge binding, signing and execution.
- `web/tests/v5-bank.test.ts`: exact displayed-draft challenge binding and expiry checks.

- `web/src/app/v5/components/page.tsx`: V5 component gallery with mock data and disabled payment execution.

- `web/src/components/v5-enquiry.tsx`: validated read-only composition tree and account, bill, history and spending blocks.

- `app/transaction_state.py`: shared single-process draft provenance, revision, audit state and HMAC receipt checks (no bank/model imports).

- `app/evidence.py`: encrypted persistent request evidence and verified timeline.
- `app/audit_http.py`: bounded HTTP audit capture and protected evidence routes.
- `tests/test_evidence.py`: persistence, redaction, retention and request timeline tests.
- `var/evidence.sqlite3`: ignored encrypted runtime evidence and metadata chain.


Bank integration runtime additions (git-ignored, under existing `var/`):
- `approvals.sqlite3`: encrypted authoritative validation context and revision lease.
- `backups/`: operator snapshots before explicit bank activation; never committed.
Operator initialization, pause/resume and audit recovery use `app/bank/gateway.py`.
