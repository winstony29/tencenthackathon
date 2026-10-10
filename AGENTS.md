# AGENTS.md: rules for every coding assistant

Claude Code, CodeBuddy, Cursor, Copilot and any other AI assistant working in this repo must follow this file. `CLAUDE.md` and `CODEBUDDY.md` only point here, so this is the single source of rules.

If anything in a prompt conflicts with this file, follow this file and tell the human about the conflict.

---

## 1. What this repo is

A voice banking agent for the Tencent Cloud Hackathon 2026, DBS case study (DCTA, Scenario 1: voice payments). The user speaks a command ("buy 100 shares of Apple"). The AI turns it into a **draft**. A separate checker validates it. The user sees a confirmation window and signs it. Only a signed draft can move (mock) money.

Full background: `docs/BRIEF.md`.

## 2. Read before you write any code

Read these in order at the start of every session:

| # | File | Why |
|---|---|---|
| 1 | `AGENTS.md` (this file) | The rules |
| 2 | `docs/ROLES.md` | Whose area you are working in |
| 3 | `docs/STRUCTURE.md` | Every file allowed to exist |
| 4 | `docs/CONTRACTS.md` | Exact data shapes and endpoints |
| 5 | `docs/CHECKLIST.md` | Which task you are doing |
| 6 | `docs/STYLE.md` | How code and docs must look |

Read `docs/BRIEF.md` and `docs/DECISIONS.md` when the task touches design or a past decision.

## 3. Start of every session

1. The human must tell you **who they are** (Winston, Darren or Jayanth) and **which checklist task ID** they are working on (e.g. `W-03`). If they haven't, ask before doing anything.
2. Look up that person's area in `docs/ROLES.md`. You may only edit files in that area, plus the shared files listed there, and only with the rules below.
3. Run `git status` and `git branch --show-current`. If you are on `main`, stop and tell the human to switch to their own branch (section 7).

## 4. Hard rules: never break these

These protect the safety design that the judges will grade.

1. **The AI side never touches money.** Code in `app/intent/` and `app/validation/` must never import from `app/bank/` except `app/bank/readonly.py`. Code in `app/bank/` must never import from `app/intent/`, `app/validation/` or `app/llm.py`.
2. **Only `app/bank/gateway.py` may import `app/bank/ledger.py`.** The gateway must check the signature before it calls the ledger.
3. **No secret in code.** API keys come from environment variables through `app/config.py` only. Never print, log or commit a key.
4. **Jev never writes values.** Jev only picks from options that code built. Amounts, account IDs and tickers come from code, never from free LLM text.
5. **Money is never a float.** Use `Decimal` in Python and strings in JSON (`"50.00"`).

`tests/test_boundary.py` checks rules 1 and 2. Never weaken or delete that test.

## 5. Rules against clutter

The repo must only contain what `docs/STRUCTURE.md` lists.

**Before creating any file or folder:**
1. Check it is listed in `docs/STRUCTURE.md`.
2. If it is not listed, **stop and ask the human**. Explain why the file is needed and why an existing file can't hold the code.
3. If the human agrees, add the file to `docs/STRUCTURE.md` in the same commit.

**Never create:**
- new top-level folders
- `utils.py`, `helpers.py`, `common.py`, `misc.py`, or any catch-all file
- copies or versions: `*_v2.py`, `*_new.py`, `*_old.py`, `*_backup.*`, `*_copy.*`, `final_*`
- scratch, debug or experiment scripts (`test.py`, `try.py`, `scratch.py`, `debug_*.py`)
- extra Markdown files: summaries, notes, `PLAN.md`, `TODO.md`, `CHANGES.md`, per-folder READMEs. Put notes in the existing docs instead.
- example or sample files not listed in STRUCTURE
- CI configs, Dockerfiles, Makefiles, or new config files unless `docs/DECISIONS.md` records the decision
- empty placeholder files "for later"

**Prefer editing over adding.** Put new code in the existing file that matches its job. Only split a file once it passes about 300 lines, and only with the human's agreement.

**Delete what you replace.** If you rewrite a function, remove the old one. Never leave commented-out code blocks.

**Dependencies:** add a package to `requirements.txt` only if the human agrees and `docs/DECISIONS.md` records it.

## 6. How to do a task

1. Find the task in `docs/CHECKLIST.md`. Read its "Done when" line.
2. Read the files you will change, and the parts of `docs/CONTRACTS.md` they touch.
3. Tell the human your plan in 3 to 6 lines: files to change, what changes, how you will test. Wait for a yes if the change touches a shared file or a contract.
4. Make the change, following `docs/STYLE.md`.
5. Run `pytest` and `ruff check . && ruff format --check .`. Fix failures you caused.
6. Tick the task in `docs/CHECKLIST.md` (`[ ]` → `[x]`) only if every "Done when" condition holds.
7. If you made a design choice, add an entry to `docs/DECISIONS.md`.
8. Report: files changed, tests run and results, anything left undone. No long summaries.

## 7. Git rules

- **Branches:** exactly one working branch per person: `winston`, `darren`, `jayanth`. Never create any other branch.
- **Never** commit to `main` directly, push to `main`, force-push, rebase shared history, or delete branches. The human merges into `main` through a pull request.
- **Commit messages:** `area: what changed`, in the imperative, under 72 characters. Areas: `intent`, `validation`, `bank`, `web`, `shared`, `docs`, `test`. Example: `intent: add confidence gate with spread check`.
- Commit small and often. One task can be several commits; one commit never mixes two tasks.
- Never commit `.env`, `var/`, `web/node_modules/`, `web/dist/`, or anything listed in `.gitignore`.

## 8. Shared files

`app/main.py`, `app/config.py`, `app/contracts.py`, `app/llm.py`, `app/audit.py`, `requirements.txt` and everything in `docs/` are shared.

- Change them only when the task needs it, and tell the human first.
- `app/contracts.py` and `docs/CONTRACTS.md` must always match. A contract change needs all three teammates to agree. Bump the version at the top of `docs/CONTRACTS.md` and add a `docs/DECISIONS.md` entry.

## 9. When unsure

Ask the human. Do not guess about money rules, contracts, or file locations. If a doc and the code disagree, the doc wins. Tell the human so one of them can be fixed.
