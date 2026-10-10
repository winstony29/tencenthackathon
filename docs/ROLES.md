# Roles

**Purpose:** who owns which code, endpoints and deliverables. An assistant may only edit files in its human's area.
**Owner:** Winston · **Last updated:** 2026-10-10

---

## 1. Summary

| Person | Job in one line | Code area | Branch |
|---|---|---|---|
| **Winston** | Turns speech into a draft (the intent engine) | `app/intent/`, `scripts/` | `winston` |
| **Darren** | Everything the user sees: voice button, questions, confirmation windows, signing | `web/` | `darren` |
| **Jayanth** | Checks the draft and moves the money: validation agent, gateway, rules, ledger, audit | `app/validation/`, `app/bank/`, `data/` | `jayanth` |

## 2. Winston: intent engine

**Diagram boxes:** speech to text, input shield, prompt rewriter, Jev × K phrasings, option builder, confidence gate, draft builder.

| Owns | Details |
|---|---|
| Files | everything in `app/intent/`; `app/llm.py`; `scripts/eval_intent.py`; `tests/test_intent.py`; `tests/commands.jsonl`; `tests/test_boundary.py`; `tests/conftest.py` |
| Endpoints | `POST /api/preview`, `POST /api/transcribe`, `POST /api/understand` |
| Shared files he keeps | `app/main.py`, `app/config.py`, `app/contracts.py`, `docs/` (merges contract changes once all agree) |
| Deliverables | Test set of ~30 commands; single-Jev vs Jev × K comparison (wrong drafts, questions asked, delay); architecture PPT; GitHub repo |
| Depends on | Jayanth's `app/bank/readonly.py` for payees, accounts, billers, tickers |

## 3. Darren: user interface

**Diagram boxes:** voice command, clarifying question, confirm & sign.

| Owns | Details |
|---|---|
| Files | everything in `web/` |
| Front-end stack | **Darren decides by Sunday 11 Oct** and records it in `docs/DECISIONS.md` (D-004). Then he lists the files of `web/` in `docs/STRUCTURE.md` before creating them. |
| Main feature | **Confirmation windows generated from the draft.** One card per action, with fields per action type as listed in `docs/CONTRACTS.md` §5. Example: stock order shows ticker, company name, buy/sell, quantity, order type, live price from `/api/quote`, estimated total, paying account. |
| Other screens | Mic button + live transcript; clarify chips (question + options + "none of these"); "frozen" warning on mismatch; result screen (executed or rejected with reason); simple audit viewer (`GET /api/audit`) |
| Signing | Creates a non-extractable device key in the browser, registers its public key once, signs each draft (see `docs/CONTRACTS.md` §4) |
| Deliverables | Public deployment link; demo video; cover image; collects everyone's CodeBuddy screenshots into one PDF; final edit of the description |
| Depends on | All endpoints in `docs/CONTRACTS.md`. Uses the stub responses until the real ones work. |

## 4. Jayanth: validation and transactions

**Diagram boxes:** validation agent, mock gateway, risk & KYC rules, mock ledger, user data, audit log.

| Owns | Details |
|---|---|
| Files | everything in `app/validation/` and `app/bank/`; `app/audit.py`; `data/bank.json`; `tests/test_validation.py`; `tests/test_bank.py` |
| Endpoints | `POST /api/validate`, `GET /api/accounts`, `GET /api/quote`, `POST /api/device/register`, `POST /api/challenge`, `POST /api/execute`, `GET /api/audit` |
| Must guarantee | Gateway rejects: missing or bad signature, reused nonce, unknown or expired nonce, nonce issued without a valid validation receipt. Ledger reachable only through the gateway. |
| Deliverables | Attack demos (unsigned request rejected, injected draft rejected, edited audit entry detected); safety + business-value part of the description |
| Depends on | Nothing; the bank side must work alone. |

## 5. Shared areas

| File | Keeper | Rule |
|---|---|---|
| `app/contracts.py` + `docs/CONTRACTS.md` | Winston | All three agree before any change |
| `app/main.py`, `app/config.py`, `requirements.txt` | Winston | Tell the group before changing |
| `app/audit.py` | Jayanth | Anyone may call it; only Jayanth changes it |
| `README.md` | Darren | Run instructions only |
| `docs/CHECKLIST.md`, `docs/DECISIONS.md` | Everyone | Tick your own tasks; add your own decisions |
