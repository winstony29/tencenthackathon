# Contracts

**Purpose:** the exact data shapes and endpoints every part of the system agrees on. `app/contracts.py` implements this file as Pydantic models. If the two disagree, this file wins.
**Owner:** Winston (keeper) · **Version:** 0.2 · **Last updated:** 2026-10-10

> Changing anything here needs all three teammates to agree. Then bump the version, update `app/contracts.py` in the same commit, and add a `docs/DECISIONS.md` entry.

---

## 1. General rules

| Rule | Example |
|---|---|
| Money is a **string** with 2 decimals; Python uses `Decimal` | `"50.00"` |
| Share quantities are integers | `100` |
| Currency is an ISO code | `"SGD"`, `"USD"` |
| Times are ISO 8601 with offset | `"2026-10-12T09:30:00+08:00"` |
| IDs have a type prefix | `u_` user, `s_` session, `d_` draft, `q_` question, `acc_` account, `pay_` payee, `bil_` biller, `n_` nonce, `vr_` receipt |
| The UI creates `session_id` (`s_` + random hex) when a new command starts, and reuses it until that command ends (draft, cancelled, blocked or unsupported). Sessions expire after 5 minutes without a call. | `"s_41b9"` |
| Text sent to the server (`text`, `transcript`) is at most 500 characters | |
| Labels are what the user reads | `"John Doe …4521"` |
| All endpoints live under `/api/` and use JSON, except `/api/transcribe` (file upload) | |
| The demo user is `u_demo` | |

## 2. The draft

A draft is everything the user will be asked to sign. Code builds it (`app/intent/draft.py`); no LLM writes it.

```json
{
  "draft_id": "d_8f2c1a",
  "user_id": "u_demo",
  "session_id": "s_41b9",
  "transcript": "send fifty to john",
  "created_at": "2026-10-12T09:30:00+08:00",
  "actions": [ /* 1 to 4 actions, run in this order */ ],
  "evidence": [
    {"question": "recipient", "pick": "pay_4521", "avg_p": 0.97, "spread": 0.03,
     "probabilities": {"pay_4521": 0.97, "pay_8892": 0.03}}
  ]
}
```

`evidence` records Jev's answers: the pick, its average probability across phrasings (`avg_p`), the spread across phrasings, and the average probability of every option. The UI may show it in a debug view (shapeshift's `?debug=1`), never on the confirmation cards.

### 2.1 Action types

**transfer**
```json
{"type": "transfer",
 "from_account_id": "acc_1234", "from_account_label": "Savings …1234",
 "to_payee_id": "pay_4521", "to_payee_label": "John Doe …4521",
 "amount": "50.00", "currency": "SGD"}
```

**bill_payment**
```json
{"type": "bill_payment",
 "from_account_id": "acc_1234", "from_account_label": "Savings …1234",
 "biller_id": "bil_0001", "biller_label": "DBS Credit Card …9012",
 "bill_reference": "4120-XXXX-XXXX-9012",
 "amount": "1240.00", "currency": "SGD"}
```

**stock_order**
```json
{"type": "stock_order",
 "from_account_id": "acc_7788", "from_account_label": "Invest (USD) …7788",
 "side": "buy", "ticker": "AAPL", "company_name": "Apple Inc.",
 "quantity": 100, "order_type": "market", "limit_price": null,
 "currency": "USD"}
```
`side` is `"buy"` or `"sell"`. `order_type` is `"market"` or `"limit"`; `limit_price` is a money string only for limit orders. The price shown to the user comes from `/api/quote` and is an estimate; it is not part of the signed draft.

## 3. Endpoints

### 3.1 AI side (Winston)

**`meta`**: attached to `/api/preview` and every `/api/understand` reply.
```json
{"latency_ms": 840, "model": "jev-1.13.0", "source": "jev", "phrasings": 4, "cached": false}
```
`source` is `jev`, `fallback` (D-005), `fake` (tests, `USE_FAKES=1`) or `none` (no model ran, e.g. `cancelled`). `phrasings` is how many texts Jev was asked about: 1 + K for `/api/understand`, 1 for `/api/preview`. `meta` feeds the latency readout and the H-01 comparison; it never changes what the UI does.

**`POST /api/preview`**: a live guess of the action type while the user types. **Display only.**
Request: `{"text": "send fifty to jo"}`
Response:
```json
{"intent": {"value": "transfer", "confidence": 0.81,
            "probabilities": {"transfer": 0.81, "bill_payment": 0.11, "stock_order": 0.05, "none": 0.03}},
 "meta": {"latency_ms": 210, "model": "jev-1.13.0", "source": "jev", "phrasings": 1, "cached": false}}
```
`value` is one of `transfer`, `bill_payment`, `stock_order`, `none` (not a banking command yet, or under 2 characters). The `intent` shape matches shapeshift's, so its `decide.ts` (ghost, choose, committed card) works with only the keys changed.
- The UI uses it only to morph the input into an empty card of the right type. It must never show values from it, build a draft from it, or skip `/api/understand` because of it.
- One Jev call on the raw text, no rewrites, cached by normalised text. It names the main action only; a multi-action command shows all its actions in the draft.

**`POST /api/transcribe`**: speech to text.
Request: multipart form, field `audio` (format agreed in D-006).
Response: `{"transcript": "send fifty to john", "confidence": 0.93}` (`confidence` may be `null`).
Typed input skips this endpoint and goes straight to `/api/understand`.

**`POST /api/understand`**: turns text into a question or a draft. Called again with the user's answer.

Request, first call: `{"user_id": "u_demo", "session_id": "s_41b9", "transcript": "send fifty to john"}`
Request, answering: `{"user_id": "u_demo", "session_id": "s_41b9", "answer": {"question_id": "q_1", "option_id": "pay_4521"}}`
Exactly one of `transcript` or `answer` is present.

**The server's gate alone decides between `question` and `draft`.** The UI shows whatever `status` says and never applies its own probability thresholds to these replies.

Responses (one of). Each also carries `meta` (above), left out of the examples below except the first.
```json
{"status": "question",
 "question": {"question_id": "q_1", "text": "Which John did you mean?",
              "options": [{"option_id": "pay_4521", "label": "John Doe …4521"},
                          {"option_id": "pay_8892", "label": "John Smith …8892"}],
              "allow_none": true},
 "evidence": [{"question": "recipient", "pick": "pay_4521", "avg_p": 0.52, "spread": 0.31,
               "probabilities": {"pay_4521": 0.52, "pay_8892": 0.48}}],
 "meta": {"latency_ms": 1840, "model": "jev-1.13.0", "source": "jev", "phrasings": 4, "cached": false}}
```
`evidence` on a question shows why the gate asked (debug view only).
```json
{"status": "draft", "draft": { /* §2 */ }}
```
```json
{"status": "blocked", "reason": "possible_injection",
 "message": "This request contains instructions I can't act on. Please say it again."}
```
```json
{"status": "unsupported", "message": "I can help with transfers, bill payments and stock orders."}
```
```json
{"status": "cancelled", "reason": "user_declined", "message": "Okay, nothing was sent."}
```
`reason` is `user_declined` (the user picked "None of these", `option_id: "none"`) or `too_many_questions` (still unsure after 3 questions; message "I couldn't work out what you meant. Please try again.").
An answer for an unknown or expired session returns 404 `not_found`; the UI starts a new command.

### 3.2 Validation (Jayanth, AI side of the boundary)

**`POST /api/validate`**: the validation agent compares the draft with its own transcript.
Request: `{"draft": { /* §2 */ }}`
Responses:
```json
{"status": "match", "receipt": "vr_3f9a…"}
```
```json
{"status": "mismatch",
 "fields": [{"field": "actions[0].amount", "draft_value": "15.00",
             "transcript_says": "fifty", "note": "amount differs"}]}
```
`receipt` is an HMAC of the draft made with `VALIDATION_RECEIPT_KEY`. The bank refuses to issue a nonce without it, so a frozen draft can never be signed.

### 3.3 Bank side (Jayanth)

**`GET /api/accounts?user_id=u_demo`**
```json
{"accounts": [{"account_id": "acc_1234", "label": "Savings …1234", "type": "savings",
               "currency": "SGD", "balance": "3200.00"}],
 "payees":   [{"payee_id": "pay_4521", "name": "John Doe", "label": "John Doe …4521"}],
 "billers":  [{"biller_id": "bil_0001", "name": "DBS Credit Card", "label": "DBS Credit Card …9012",
               "amount_due": "1240.00"}],
 "holdings": [{"ticker": "AAPL", "quantity": 10}]}
```

**`GET /api/quote?ticker=AAPL`**
```json
{"ticker": "AAPL", "company_name": "Apple Inc.", "price": "227.50",
 "currency": "USD", "as_of": "2026-10-12T09:30:00+08:00"}
```
Prices are mock data from `data/bank.json`.

**`POST /api/device/register`**: stores the phone's public key.
Request: `{"user_id": "u_demo", "public_key_jwk": { /* EC P-256 public JWK */ }}`
Response: `{"status": "registered"}`

**`POST /api/challenge`**: issues a one-time nonce for a validated draft.
Request: `{"draft": { /* §2 */ }, "receipt": "vr_3f9a…"}`
Response:
```json
{"nonce": "n_7d21…", "signing_payload": "<canonical draft JSON>.n_7d21…",
 "expires_at": "2026-10-12T09:35:00+08:00"}
```
The bank stores the draft against the nonce. Nonces expire after 5 minutes and work once.

**`POST /api/execute`**: the only way money moves.
Request: `{"nonce": "n_7d21…", "signature": "<base64url>", "otp": null}`
Responses:
```json
{"status": "executed", "results": [{"action_index": 0, "reference": "TX-000123"}]}
```
```json
{"status": "needs_extra_check", "method": "otp",
 "message": "Amounts over SGD 5,000 need a one-time code."}
```
```json
{"status": "rejected", "reason": "bad_signature", "message": "Signature check failed."}
```
`reason` is one of: `bad_signature`, `unknown_nonce`, `nonce_used`, `nonce_expired`, `limit_exceeded`, `velocity_exceeded`, `kyc_incomplete`, `insufficient_funds`, `bad_otp`.
On `needs_extra_check`, the UI asks for the code and calls `/api/execute` again with the same nonce and `otp` set. The demo code is set in config (D-007).

**`GET /api/audit?limit=50`**
```json
{"chain_valid": true,
 "entries": [{"index": 41, "time": "…", "actor": "intent", "event": "jev_answer",
              "data": {}, "prev_hash": "…", "hash": "…"}]}
```
Every area writes events through `app/audit.py`: `append(actor: str, event: str, data: dict) -> None`, where `actor` is `intent`, `validation` or `bank`. Event names are in §8.

## 4. Signing

1. On first load, the UI creates an ECDSA P-256 key pair with WebCrypto, `extractable: false`, stores it in IndexedDB, and sends the public key to `/api/device/register`.
2. For each draft, the UI signs the **exact `signing_payload` string** from `/api/challenge` (UTF-8 bytes) with `{name: "ECDSA", hash: "SHA-256"}`.
3. WebCrypto outputs a 64-byte raw signature (r‖s). The UI sends it base64url-encoded. The gateway converts it to DER before verifying with `cryptography`.
4. Canonical draft JSON is `json.dumps(draft, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Only the server builds it; the UI never re-serialises the draft.

## 5. Confirmation window: what each card shows

One card per action, in draft order. One **Confirm & sign** button signs the whole draft; **Cancel** discards it.

| Action type | Card title | Fields, in this order |
|---|---|---|
| transfer | Transfer | To (`to_payee_label`) · Amount (`amount` + `currency`) · From (`from_account_label`) |
| bill_payment | Pay bill | Biller (`biller_label`) · Reference (`bill_reference`) · Amount · From |
| stock_order | Buy shares / Sell shares | Stock (`ticker` · `company_name`) · Quantity · Order type (market / limit + `limit_price`) · Price now (from `/api/quote`, marked "estimate") · Estimated total (quantity × price, marked "estimate") · Pay from / Pay into |

Other states the UI must show: **preview** (empty card of the guessed type while typing, from `/api/preview`), **thinking** (waiting for `/api/understand`), **question** (text + one chip per option + "None of these"), **blocked**, **unsupported**, **frozen** (mismatch, listing each field), **needs extra check** (code entry), **executed** (references), **rejected** (plain-English reason).

## 6. Errors

Malformed requests return HTTP 400 with `{"error": {"code": "bad_request", "message": "…"}}`. Unknown user or session returns 404 with code `not_found`. Upstream failures (Jev, LLM, ASR down) return 502 with code `upstream_error`. Business outcomes such as `rejected` or `mismatch` return HTTP 200 with the status shown above.

## 7. Stubs

Until a real endpoint works, its router returns the example responses in this file unchanged. Darren builds the UI against the stubs from day one.

## 8. Audit events

Intent side (`actor: "intent"`). Jayanth adds the validation and bank events here. `data` never holds keys or secrets. `/api/preview` calls are not logged: they are display only and too frequent.

| `event` | `data` |
|---|---|
| `transcribed` | `transcript`, `confidence` |
| `shield_checked` | `session_id`, `flagged`, `p` |
| `jev_answer` | `session_id`, `question`, `phrasings` (each: `text`, `probabilities`), `avg_p`, `spread` |
| `gate_decision` | `session_id`, `decision` (`proceed` or `ask`), `failing_question`, `tau`, `delta` |
| `question_asked` | `session_id`, `question_id`, `options` |
| `answer_received` | `session_id`, `question_id`, `option_id` |
| `draft_built` | `session_id`, `draft_id` |
| `blocked` | `session_id`, `reason` |
| `cancelled` | `session_id`, `reason` |

## 9. Timing

Everything is triggered by the user. The only repeating call is the stock price while a stock card is open. Numbers are starting values, tuned in testing.

| Call | When | How |
|---|---|---|
| `/api/preview` | The typed text changes | Debounce 150 ms; abort the previous request; skip under 2 characters; on error keep the last card |
| Recording | The user taps the mic | Stops on a second tap or after about 1.5 s of silence |
| `/api/transcribe` | Recording stops | Once |
| `/api/understand` | Each user turn: transcript, or a tapped chip | Once; show **thinking**; give up after 15 s |
| `/api/validate` → `/api/challenge` → `/api/execute` | A draft arrives; the user taps **Confirm & sign** | In sequence |
| `/api/quote` | A stock card is on screen | Every 10 s |
| `/api/audit` | The audit page opens or is refreshed | Once |
| **Cancel** | Any time | Abort the call in flight and drop the `session_id`; the next command gets a new one |

Voice: if the browser's built-in speech recognition gives live text, the UI may send it to `/api/preview` while the user speaks. Only the Tencent ASR transcript goes to `/api/understand`.
