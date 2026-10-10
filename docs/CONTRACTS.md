# Contracts

**Purpose:** the exact data shapes and endpoints every part of the system agrees on. `app/contracts.py` implements this file as Pydantic models. If the two disagree, this file wins.
**Owner:** Winston (keeper) · **Version:** 0.1 · **Last updated:** 2026-10-10

> Changing anything here needs all three teammates to agree. Then bump the version, update `app/contracts.py` in the same commit, and add a `docs/DECISIONS.md` entry.

---

## 1. General rules

| Rule | Example |
|---|---|
| Money is a **string** with 2 decimals; Python uses `Decimal` | `"50.00"` |
| Share quantities are integers | `100` |
| Currency is an ISO code | `"SGD"`, `"USD"` |
| Times are ISO 8601 with offset | `"2026-10-12T09:30:00+08:00"` |
| IDs have a type prefix | `u_` user, `s_` session, `d_` draft, `acc_` account, `pay_` payee, `bil_` biller |
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
    {"question": "recipient", "pick": "pay_4521", "avg_p": 0.97, "spread": 0.03}
  ]
}
```

`evidence` records Jev's answers for the audit log. The UI does not show it.

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

**`POST /api/transcribe`**: speech to text.
Request: multipart form, field `audio` (format agreed in D-006).
Response: `{"transcript": "send fifty to john", "confidence": 0.93}` (`confidence` may be `null`).
Typed input skips this endpoint and goes straight to `/api/understand`.

**`POST /api/understand`**: turns text into a question or a draft. Called again with the user's answer.

Request, first call: `{"user_id": "u_demo", "session_id": "s_41b9", "transcript": "send fifty to john"}`
Request, answering: `{"user_id": "u_demo", "session_id": "s_41b9", "answer": {"question_id": "q_1", "option_id": "pay_4521"}}`
Exactly one of `transcript` or `answer` is present.

Responses (one of):
```json
{"status": "question",
 "question": {"question_id": "q_1", "text": "Which John did you mean?",
              "options": [{"option_id": "pay_4521", "label": "John Doe …4521"},
                          {"option_id": "pay_8892", "label": "John Smith …8892"}],
              "allow_none": true}}
```
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
{"status": "cancelled", "message": "Okay, nothing was sent."}
```
`cancelled` is returned when the user picks "none of these" (`option_id: "none"`).

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

Other states the UI must show: **question** (text + one chip per option + "None of these"), **blocked**, **unsupported**, **frozen** (mismatch, listing each field), **needs extra check** (code entry), **executed** (references), **rejected** (plain-English reason).

## 6. Errors

Malformed requests return HTTP 400 with `{"error": {"code": "bad_request", "message": "…"}}`. Unknown user or session returns 404 with code `not_found`. Upstream failures (Jev, LLM, ASR down) return 502 with code `upstream_error`. Business outcomes such as `rejected` or `mismatch` return HTTP 200 with the status shown above.

## 7. Stubs

Until a real endpoint works, its router returns the example responses in this file unchanged. Darren builds the UI against the stubs from day one.
