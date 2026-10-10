# Style

**Purpose:** how code and documentation must look, so the repo reads as if one person wrote it.
**Owner:** Winston · **Last updated:** 2026-10-10

---

## 1. Python

| Topic | Rule |
|---|---|
| Version | Python 3.11+ |
| Formatting | `ruff format` (line length 100). `ruff check` must pass. |
| Types | Type hints on every function signature. Pydantic v2 models for anything crossing an endpoint, all defined in `app/contracts.py`. |
| Names | `snake_case` functions and variables, `PascalCase` classes, `UPPER_CASE` constants. Full words: `average_probability`, not `avg_p` (JSON field names in CONTRACTS are the exception). |
| Money | `Decimal`, never `float`. Convert to string only at the JSON edge. |
| Config | Every constant (thresholds, K, limits, timeouts) lives in `app/config.py` with a one-line comment saying what it controls. No magic numbers in other files. |
| Errors | Raise `fastapi.HTTPException` with the codes in `CONTRACTS.md` §6. Never swallow an exception silently. |
| Logging | Important events go to `app/audit.py`. No `print()` in committed code. |
| External calls | Use `httpx` with a timeout. Each external service has exactly one client file (`jev.py`, `llm.py`, `asr.py`). |
| Async | Endpoints are `async def`. External calls use `httpx.AsyncClient`. |
| Function size | Aim for under 40 lines. If longer, split into private helpers (`_build_options`) in the same file. |

### 1.1 File header

Every Python file starts with a docstring in this exact shape:

```python
"""Confidence gate: decides whether to ask the user or proceed.

Owner: Winston
Diagram box: Confidence gate (Intent detection column)
Contract: none; called by app/intent/router.py
"""
```

### 1.2 Function docstrings

Public functions get a short docstring in plain English. Say what it does and what it returns. Mention edge cases. Don't restate the type hints.

```python
def decide(answers: list[JevAnswer], amount: Decimal) -> GateResult:
    """Decide whether Jev's answers are trustworthy enough to build a draft.

    Proceeds only if every question's average probability is at least tau
    (which rises with the amount) and the spread across rewrites is at most delta.
    Returns the first failing question so the UI can ask about it.
    """
```

### 1.3 Comments

Comments explain **why**, not what. Good: `# Keep the original transcript as R0 so a bad rewrite can't remove the user's words.` Bad: `# loop over answers`.

## 2. Tests

- `pytest`; one test file per area (see STRUCTURE).
- Test names describe behaviour: `test_gate_asks_when_rewrites_disagree`.
- Tests never call real Jev, LLM or ASR. Use the fakes in `tests/conftest.py`.
- Every safety promise in `docs/BRIEF.md` §4 has at least one test.

## 3. Front end (once Darren chooses the stack)

- Every field shown in a confirmation card comes from the draft or `/api/quote`. The UI never computes or edits money values, except the estimated total, which is labelled "estimate".
- The UI never re-serialises the draft for signing. It signs `signing_payload` exactly as received.
- Text the user reads is plain, short and specific: "Which John did you mean?", not "Ambiguity detected in recipient field".
- Darren adds framework-specific rules here when D-004 is recorded.

## 4. Documentation

| Rule | Detail |
|---|---|
| Language | Plain English. Short sentences. Define any technical term the first time it appears. |
| Header | Every doc starts with the title, then **Purpose**, **Owner**, **Last updated**. |
| Layout | Numbered sections (`## 1.`, `### 1.1`). Tables for anything with more than two attributes. |
| Code blocks | Use a language tag (` ```python `, ` ```json `). |
| Numbers | Mark guesses and made-up example numbers as such. |
| Where things go | Rules → `AGENTS.md`. Shapes → `CONTRACTS.md`. Files → `STRUCTURE.md`. Tasks → `CHECKLIST.md`. Choices and their reasons → `DECISIONS.md`. Never create a new doc for something that fits one of these. |

## 5. Commits

`area: what changed` in the imperative, under 72 characters. Areas: `intent`, `validation`, `bank`, `web`, `shared`, `docs`, `test`.

Good: `bank: reject reused nonces in gateway`
Bad: `fixed stuff`, `WIP`, `update`
