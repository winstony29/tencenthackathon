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
│   └── eval_intent.py        single Jev vs Jev × K comparison report           Winston
│
└── var/                      runtime state (ledger, audit, nonces); git-ignored
```

## 2. `web/` files

Empty until Darren records the front-end stack in `docs/DECISIONS.md` (D-004). He then lists every file and folder of `web/` here before creating it. Build output (`web/dist/`) and `web/node_modules/` are never committed.

```
web/
└── (to be listed by Darren)
```

## 3. Screenshot naming

`docs/screenshots/<name>_<yyyy-mm-dd>_<n>.png`, for example `winston_2026-10-11_1.png`. Only CodeBuddy/WorkBuddy proof goes here.

## 4. Change log for this file

| Date | Who | Change |
|---|---|---|
| 2026-10-10 | Winston | First version |
| 2026-10-10 | Winston | Add `docs/GITHUB.md` |
| 2026-10-10 | Winston | Add `docs/DCTA_endpoints_and_data.pptx` |
