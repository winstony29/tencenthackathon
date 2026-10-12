# Contracts

**Purpose:** the exact data shapes and endpoints every part of the system agrees on. `app/contracts.py` implements this file as Pydantic models. If the two disagree, this file wins.
**Owner:** Winston (keeper) · **Version:** 0.4 · **Last updated:** 2026-10-13

> Changing anything here needs all three teammates to agree. Then bump the version, update `app/contracts.py` in the same commit, and add a `docs/DECISIONS.md` entry.

---

## 1. General rules

| Rule | Example |
|---|---|
| Money is a **string** with 2 decimals; Python uses `Decimal` | `"50.00"` |
| Share quantities are integers | `100` |
| Currency is an ISO code | `"SGD"`, `"USD"` |
| Any amount the user says is **SGD unless they name another currency**. This applies to every action. | "2k", "$2,000", "S$2,000" are SGD; "US$2,000", "2,000 USD", "2,000 US dollars" are USD |
| Times are ISO 8601 with offset | `"2026-10-12T09:30:00+08:00"` |
| IDs have a type prefix | `u_` user, `s_` session, `d_` draft, `q_` question, `acc_` account, `pay_` payee, `bil_` biller, `n_` nonce, `vr_` receipt |
| The UI creates `session_id` (`s_` + random hex) when a new conversation starts. A draft does not end it: reuse it for follow-up turns about that draft (§3.1). While a question is open, keep it for spoken or typed replies too (§3.1). Cancelled, blocked and unsupported end it. Sessions expire after 5 minutes without a call. A UI that never sends follow-ups may start a new session per command. | `"s_41b9"` |
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

Actions can include an `fx_conversion`. Code builds it; the user never says it. It still counts within the 1 to 4 actions.

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

**By a sum of money.** "Invest 2k in Apple", "buy $2,000 of Apple" and "sell US$1,000 of Tesla" become a normal `stock_order`. Code, not Jev, turns the sum into a whole number of shares at the current quote price, rounding down. The shape is unchanged and carries only `quantity`.
- A USD sum for a USD share is bought from Invest (USD) …7788 directly: US$2,000 at 227.50 is `"quantity": 8`.
- An SGD sum for a USD share adds an `fx_conversion` first. Example: 2,000.00 SGD from Savings …1234 × 0.74 = 1,480.00 USD into Invest, then 1,480.00 / 227.50 gives `"quantity": 6`.
- Selling for an SGD sum works out the shares from the sum converted at the same rate. It adds no conversion, because the proceeds stay in USD.
- The SGD comes from the account the user names, otherwise Savings, the same default as transfers.
- The stock card (§5) is unchanged. Its estimated total (quantity × price) is at most the sum said.
- A sum that buys no whole share is `unsupported`, with a message such as "S$100 doesn't buy one Apple share (227.50 USD)."

**fx_conversion**
```json
{"type": "fx_conversion",
 "from_account_id": "acc_1234", "from_account_label": "Savings …1234",
 "to_account_id": "acc_7788", "to_account_label": "Invest (USD) …7788",
 "sell_amount": "2700.00", "sell_currency": "SGD",
 "buy_amount": "1998.00", "buy_currency": "USD",
 "rate": "0.740000", "rate_as_of": "2026-10-13T09:30:00+08:00"}
```
`sell_amount` and `buy_amount` are money strings. `rate` is a decimal string with 6 places: one unit of `sell_currency` buys `rate` units of `buy_currency`. `rate_as_of` is an ISO time. `buy_amount` = `sell_amount` × `rate`, rounded down to 2 places. Code adds it only when the money and the purchase are in different currencies. It never counts against the limit of one transfer, one bill payment and one share order. The rates come from `GET /api/fx` (§3.3).

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
- One Jev call on the text with numbers swapped for stand-ins (D-026), no rewrites, cached by normalised text. It names the main action only; a multi-action command shows all its actions in the draft.

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
{"status": "draft", "draft": { /* §2 */ }, "objective": "prepare", "scenario": null}
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

**Spoken answers.** While a question is open (the last reply in the session had `status: "question"`), a new `transcript` with the same `session_id` is read as the answer to that question. The request is unchanged: `{"user_id": "u_demo", "session_id": "s_41b9", "transcript": "John Smith"}`. Tapping a chip still sends `answer` with an `option_id`. Jev picks from the offered options, or says the user rejects them all, or says the user is asking for something different. The replies use the existing shapes:
- A sure pick of an offered option: the same reply as tapping that chip (usually the `draft`, or the next `question`).
- "None of these": `cancelled` with reason `user_declined`.
- Something different ("actually, pay my bill"): handled as a first command in the same session. The open question is dropped.
- Unsure: the same question again with a new `question_id`. It counts towards the 3-question limit, so `too_many_questions` can follow.

For "Which John did you mean?" (John Doe …4521, John Smith …8892): "John Smith", "Smith", "the second one" and "the one ending 8892" answer it; "neither" cancels; "actually pay my credit card bill" starts over.

**"The rest".** "The rest", "what's left", "the remaining" and "leftover" mean the balance left after the actions said before it in the same command, computed by code. "All" and "everything" stay the whole balance. If no account is named, "the rest" is taken from the account the earlier action draws from. Jev picks the account when it is unclear.

Example: "send 500 to John Doe and invest the rest in Apple", with Savings …1234 at 3,200.00 SGD and a mock rate of 1 SGD = 0.74 USD. The draft has three actions, in spoken order:
```json
[
 {"type": "transfer",
  "from_account_id": "acc_1234", "from_account_label": "Savings …1234",
  "to_payee_id": "pay_4521", "to_payee_label": "John Doe …4521",
  "amount": "500.00", "currency": "SGD"},
 {"type": "fx_conversion",
  "from_account_id": "acc_1234", "from_account_label": "Savings …1234",
  "to_account_id": "acc_7788", "to_account_label": "Invest (USD) …7788",
  "sell_amount": "2700.00", "sell_currency": "SGD",
  "buy_amount": "1998.00", "buy_currency": "USD",
  "rate": "0.740000", "rate_as_of": "2026-10-13T09:30:00+08:00"},
 {"type": "stock_order",
  "from_account_id": "acc_7788", "from_account_label": "Invest (USD) …7788",
  "side": "buy", "ticker": "AAPL", "company_name": "Apple Inc.",
  "quantity": 8, "order_type": "market", "limit_price": null,
  "currency": "USD"}
]
```
The rest of Savings is 3,200.00 − 500.00 = 2,700.00 SGD. All of it is converted: 2,700.00 × 0.74 = 1,998.00 USD, rounded down to the cent. Code buys as many whole shares as that covers: 1,998.00 / 227.50 gives 8 shares. The roughly 178.00 USD left over stays in Invest (USD). "Sell 10 Apple and buy Tesla with the rest" stays in USD: no conversion.

**`objective`** (every `draft` reply) says what the user wants to do with the draft, so the UI can arrange the screen (Darren's `/v3`): `prepare`, `assess_affordability`, `simulate`, `check_recipient` or `review`. Jev picks it. On a first command an unsure pick becomes `prepare`.

**Follow-up turns.** After a draft, a new `transcript` with the same `session_id` is a follow-up about that draft ("Can I afford that?", "What if I send $700 instead?", "Make it $300 from savings", "Review the transfer"). Jev picks the objective and, for each current action, a new value or "keep"; it may ask a clarifying question first, as above. Then:
- Changes are applied by code. The reply carries the updated draft with the same `draft_id`, and its `transcript` holds every turn, one per line, later lines amending earlier ones, so validation sees everything the user said. An updated draft must be validated again before signing.
- For `simulate`, changes go into `scenario` instead and the draft is unchanged. `scenario` maps an action index to hypothetical values: `{"0": {"amount": "700.00"}}` or `{"1": {"quantity": "80"}}`. It is never sent to `/api/validate`, `/api/challenge` or `/api/execute`; the UI only shows it. It is `null` for every other objective.
- A follow-up that is really a new command (Jev picks `new_task`) starts over: the reply is a fresh draft for the new transcript alone.

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
Some values in a draft are derived by code, not said: quantities bought by a sum of money, "the rest" amounts and `fx_conversion` amounts. The validator re-derives them from balances, quotes and rates through `readonly` (§3.3), and reports a mismatch only if its own result differs. For example, the transcript says "2k" while the draft says 6 shares: that is a match if the quote gives 6.

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

**`GET /api/fx?from=SGD&to=USD`**
```json
{"from": "SGD", "to": "USD", "rate": "0.740000", "as_of": "2026-10-13T09:30:00+08:00"}
```
Rates are mock data in `data/bank.json` (at least SGD→USD and USD→SGD). An unknown pair returns 404 `not_found`. *Pending: until Jayanth builds it, the stub returns this example (§7).*

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
`reason` is one of: `bad_signature`, `unknown_nonce`, `nonce_used`, `nonce_expired`, `limit_exceeded`, `velocity_exceeded`, `kyc_incomplete`, `insufficient_funds`, `bad_otp`, `rate_changed`.
```json
{"status": "rejected", "reason": "rate_changed", "message": "The exchange rate changed. Please confirm again."}
```
The gateway rejects a draft whose `fx_conversion` `rate` differs from the bank's current rate for that pair. The UI starts a new command, as for other rejections.
On `needs_extra_check`, the UI asks for the code and calls `/api/execute` again with the same nonce and `otp` set. The demo code is set in config (D-007).

**`GET /api/audit?limit=50`**
```json
{"chain_valid": true,
 "entries": [{"index": 41, "time": "…", "actor": "intent", "event": "jev_answer",
              "data": {}, "prev_hash": "…", "hash": "…"}]}
```
Bank rules for `fx_conversion` (*pending: Jayanth builds these*):
- **Ledger:** takes `sell_amount` from `from_account_id` and adds `buy_amount` to `to_account_id`, before the actions after it.
- **Rules:** limits and the extra-check threshold apply to `fx_conversion` like a transfer, measured in `sell_currency`.

The intent engine reads user data only through `app/bank/readonly.py` (AGENTS §4), which provides:
`get_accounts(user_id: str) -> AccountsResponse` (the same shape as `GET /api/accounts`), `list_quotes() -> list[QuoteResponse]` (every tradable ticker), `get_bill_reference(biller_id: str) -> str`, and `get_fx_rate(from_currency: str, to_currency: str) -> FxResponse` (the same shape as `GET /api/fx`; pending, like the endpoint).

Every area writes events through `app/audit.py`: `append(actor: str, event: str, data: dict) -> None`, where `actor` is `intent`, `validation` or `bank`. Event names are in §8.

## 4. Signing

1. On first load, the UI creates an ECDSA P-256 key pair with WebCrypto, `extractable: false`, stores it in IndexedDB, and sends the public key to `/api/device/register`.
2. For each draft, the UI signs the **exact `signing_payload` string** from `/api/challenge` (UTF-8 bytes) with `{name: "ECDSA", hash: "SHA-256"}`.
3. WebCrypto outputs a 64-byte raw signature (r‖s). The UI sends it base64url-encoded. The gateway converts it to DER before verifying with `cryptography`.
4. Canonical draft JSON is `json.dumps(draft, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Only the server builds it; the UI never re-serialises the draft.

## 5. Confirmation window: what each card shows

One card per action, in draft order. A **Convert currency** card sits between the other cards in draft order. One **Confirm & sign** button signs the whole draft; **Cancel** discards it.

| Action type | Card title | Fields, in this order |
|---|---|---|
| transfer | Transfer | To (`to_payee_label`) · Amount (`amount` + `currency`) · From (`from_account_label`) |
| bill_payment | Pay bill | Biller (`biller_label`) · Reference (`bill_reference`) · Amount · From |
| fx_conversion | Convert currency | From (`from_account_label`) · Sell (`sell_amount` + `sell_currency`) · Rate ("1 SGD = 0.740000 USD", with `rate_as_of`) · Get (`buy_amount` + `buy_currency`) · Into (`to_account_label`) |
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
| `answer_received` | `session_id`, `question_id`, `option_id`, `via` (`chip` or `speech`) |
| `draft_built` | `session_id`, `draft_id` |
| `blocked` | `session_id`, `reason` |
| `cancelled` | `session_id`, `reason` |

In `jev_answer`, `phrasings[].text` is the stand-in text Jev saw, with numbers swapped for stand-ins (D-026), not the transcript. Jayanth adds `fx_converted` (actor `bank`) with `draft_id`, `sell_amount`, `sell_currency`, `buy_amount`, `buy_currency`, `rate`.

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

## 10. Changes in v0.4

- **Spoken answers:** while a question is open, a `transcript` in the same session answers it (§3.1).
- **Sums of money:** "invest 2k in Apple" becomes a `stock_order` with a quantity computed by code (§2.1).
- **"The rest":** means what is left after the actions said before it (§3.1).
- **Currency conversion:** new `fx_conversion` action (§2.1), `GET /api/fx` and `get_fx_rate` (§3.3), and reject reason `rate_changed`. The intent side is built; the bank side is pending.
- **Amounts are SGD by default** unless the user names another currency (§1).
- **Already live (D-026):** Jev never sees real amounts. Every number the user says is swapped for a stand-in, and balances never appear in Jev's options (§3.1, §8). Share counts and limit prices are only numbers the user said, never a fraction of a balance.
- **Testing:** `scripts/chat.py` runs a text conversation with `/api/understand` against live Jev, turn by turn (`.venv/bin/python scripts/chat.py -v`).
