# Contracts

**Purpose:** the exact data shapes and endpoints every part of the system agrees on. `app/contracts.py` implements this file as Pydantic models. If the two disagree, this file wins.
**Owner:** Winston (keeper) · **Version:** 0.15 (integrated browser ownership and privacy guards) · **Last updated:** 2026-10-16

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

**account_transfer** (*v0.7, proposed*): moves money between two of the user's own accounts in one currency ("move 500 from current to savings").
```json
{"type": "account_transfer",
 "from_account_id": "acc_5678", "from_account_label": "Current …5678",
 "to_account_id": "acc_1234", "to_account_label": "Savings …1234",
 "amount": "500.00", "currency": "SGD"}
```
`to_account_id` differs from `from_account_id`; both accounts are in `currency`. A move between accounts in different currencies ("move 1,000 from savings to invest") is a standalone `fx_conversion` instead (below). At most one `account_transfer` per draft, like the other types. Whether limits and the extra check apply to it is a bank rule (D-007).

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
`sell_amount` and `buy_amount` are money strings. `rate` is a decimal string with 6 places: one unit of `sell_currency` buys `rate` units of `buy_currency`. `rate_as_of` is an ISO time. `buy_amount` = `sell_amount` × `rate`, rounded down to 2 places. Code adds it when the money and the purchase are in different currencies, and (*v0.7, proposed*) on its own when the user moves money between their own accounts in different currencies or asks to convert ("convert 1,000 SGD to USD"). It never counts against the limit of one transfer, one bill payment and one share order. The rates come from `GET /api/fx` (§3.3).

## 3. Endpoints

### 3.1 AI side (Winston)

**`meta`**: attached to `/api/preview` and every `/api/understand` reply.
```json
{"latency_ms": 840, "model": "jev-1.13.0", "source": "jev", "phrasings": 4, "cached": false}
```
`source` is `jev`, `fallback` (D-005), `fake` (tests, `USE_FAKES=1`) or `none` (no model ran, e.g. `cancelled`). `phrasings` is how many texts Jev was asked about: 1 + K for `/api/understand`, 1 for `/api/preview`. `meta` feeds the latency readout and the H-01 comparison; it never changes what the UI does.

**`POST /api/preview`**: a live guess of the action type while the user types. **Display only.**
Request: `{"text": "send fifty to jo", "user_id": "u_demo"}`. `user_id` is optional; with it, the user's payee names reach Jev only as pseudonyms (D-039), without it they pass through, so UIs send it.
Response:
```json
{"intent": {"value": "transfer", "confidence": 0.81,
            "probabilities": {"transfer": 0.81, "bill_payment": 0.11, "stock_order": 0.05, "none": 0.03}},
 "meta": {"latency_ms": 210, "model": "jev-1.13.0", "source": "jev", "phrasings": 1, "cached": false}}
```
`value` is one of `transfer`, `bill_payment`, `stock_order`, `none` (not a banking command yet, or under 2 characters). The `intent` shape matches shapeshift's, so its `decide.ts` (ghost, choose, committed card) works with only the keys changed.
- The UI uses it only to morph the input into an empty card of the right type. It must never show values from it, build a draft from it, or skip `/api/understand` because of it.
- One Jev call on the text with numbers swapped for stand-ins (D-026), no rewrites, cached by normalised text per browser (§14). It names the main action only; a multi-action command shows all its actions in the draft.

**`POST /api/transcribe`**: speech to text.
Request: multipart form, field `audio` (16 kHz mono 16-bit PCM WAV, D-006), optional field `user_id` (default `u_demo`, must match `u_…`). The `user_id` supplies hotwords from that user's bank data to improve recognition accuracy.
Response: `{"transcript": "send fifty to john", "confidence": null}`. The international-site Tencent ASR API does not return a confidence score, so `confidence` is always `null`.
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
An answer for an unknown or expired session returns 404 `not_found`; the UI starts a new command. So does any request whose `session_id` another browser started (§14).

**Answers** (*v0.6, proposed*). A question about the user's own data, or a market figure, that moves no money gets an `answer`, not a draft:
```json
{"status": "answer", "kind": "balance",
 "accounts": [{"account_id": "acc_1234", "label": "Savings …1234", "type": "savings",
               "currency": "SGD", "balance": "3200.00"}],
 "meta": {"latency_ms": 900, "model": "jev-1.13.0", "source": "jev", "phrasings": 1, "cached": false}}
```
Each `kind` carries one payload field:

| `kind` | Example question | Jev picks (from options code built) | Payload |
|---|---|---|---|
| `balance` | "What's my balance?", "How much is in savings?" | account, or all | `accounts`: as in `GET /api/accounts` |
| `transactions` | "What did I spend this week?", "What did I pay Mum last month?", "Biggest spend this month?" | account, or all; period: `latest` (5 most recent, the default), `days` (the number of days or weeks said, 7 when none), `this_month`, `last_month` (calendar months); payee, or any; direction: `all`, `out`, `in`; order: `newest` (default) or `largest` (ranked by SGD value) | `transactions`: `transaction_id`, `time`, `account_id`, `account_label`, `description`, `direction` (`credit` or `debit`), `amount`, `currency`, `payee_id` (`null` when not a payee) |
| `bills` | "What bills are coming up?", "What are my recurring bills?" | scope: `all` or `recurring` | `bills`: `label`, `amount`, `currency`, `due_at`, `account_id`, `account_label`, `recurrence` (`weekly`, `monthly`, `yearly` or `null`), soonest first; every unpaid expense in the schedule, including the card bill |
| `holdings` | "What shares do I have?" | nothing | `holdings`: `ticker`, `company_name`, `quantity`, `price`, `currency`, `value` (quantity × price, an estimate), `as_of` |
| `quote` | "What's Apple trading at?" | ticker | `quotes`: as in `GET /api/quote` |
| `fx_rate` | "What's the SGD to USD rate?" | currency pair | `rate`: as in `GET /api/fx` |
| `payees` | "Who can I send money to?" | nothing | `payees`: as in `GET /api/accounts` |
| `help` | "What can you do?" | nothing | `message`: a fixed list of what the agent can do |

- One Jev question (`info`) says which kind is asked, or none; further questions pick the filters that kind takes (table above). Filters combine, so a few of them cover many questions; code runs the query. Jev never sees a figure. Code reads every value through `readonly` (D-026 still holds).
- An `answer` is never validated, signed or executed. It ends the session, like `unsupported`; the next command gets a new `session_id`.
- If the same command also asks to move money ("what's in savings and send 50 to John"), the reply is the `draft`; the cards already show each account.
- An unsure pick asks a normal `question` first ("What would you like to know?", "Which account?").
- A UI shows a `kind` it doesn't know as `unsupported`, so later kinds don't break older UIs.
- Jayanth adds `list_transactions(user_id) -> list[dict]` and `list_bills(user_id) -> list[dict]` to `readonly` (§3.3).

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
Rates are mock data in `data/bank.json` (at least SGD→USD and USD→SGD). An unknown pair returns 404 `not_found`. The UI does not need it: every `fx_conversion` in a draft already carries its `rate`.

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
`get_accounts(user_id: str) -> AccountsResponse` (the same shape as `GET /api/accounts`), `list_quotes() -> list[QuoteResponse]` (every tradable ticker), `get_bill_reference(biller_id: str) -> str`, and `get_fx_rate(from_currency: str, to_currency: str) -> FxResponse` (the same shape as `GET /api/fx`). `get_bill_reference` and `get_fx_rate` raise `LookupError` for an unknown biller or pair.

Every area writes events through `app/audit.py`: `append(actor: str, event: str, data: dict) -> None`, where `actor` is `intent`, `validation` or `bank`. Event names are in §8.

### 3.4 Presentation (Darren)

Jev arranges the screen: it picks a layout and which supporting panels to show. **It never sees or changes the transaction.** The request carries only categories, never names, amounts, IDs or the transcript (D-021). The server offers Jev only choices it has checked are allowed, and checks every answer again; the UI checks the result once more and falls back to a fixed layout on any error. Card contents always come from the draft (§5). The models live in `web/presentation_v4.py`, not `app/contracts.py`; Darren keeps them.

**`POST /api/presentation-v4/evaluate`** (V4 and V5): one stage of composing a screen. V5 calls it twice per screen: first to choose the layout and panels, then to place and order them.
Request:
```json
{"context": {"objective": "prepare", "action_types": ["transfer"],
             "financial_context": false, "funding": "sufficient", "commitments": "unknown",
             "reserve": "unknown", "alternatives": true, "viewport": "desktop"},
 "selected": [],
 "questions": {"root": ["Workspace_all_focus", "unavailable"], "select_0": ["omit", "use:…"]}}
```
- `objective`: `prepare`, `assess_affordability`, `simulate` or `review`. The UI sends `check_recipient` as `prepare`.
- `action_types`: 1 to 4 of `transfer`, `bill_payment`, `stock_order`, `fx_conversion`, in draft order.
- `funding` is `sufficient`, `shortfall` or `unknown`; `commitments` is `present`, `none` or `unknown`; `reserve` is `retained`, `breached` or `unknown`. `alternatives` says whether another account in the same currency exists.
- `selected` is empty for the first stage. For the second it lists the panel IDs chosen in the first, layout first.
- `questions` maps each question to the option IDs the UI offers. It must equal the set the server derives from `context` and `selected` from the catalog in `web/v4-catalog.json`, or the request is refused.

Response:
```json
{"answers": {"root": {"choice": "Workspace_all_focus", "confidence": 0.93}},
 "model": "jev-1.13.0", "usage": {"input_tokens": 812, "output_tokens": 40}, "latency_ms": 640}
```
Errors: 400 `bad_request` for a context or question set outside the catalog; 503 `upstream_error` when the server has no Jev key; 502 `upstream_error` when Jev fails or answers outside the offered choices. On any error the UI uses its fixed layout.

**Older routes**, kept for V2 and V3, follow the same rules with their own catalogs: `POST /api/presentation-demo` (V2, `web/jev.py`) and `POST /api/presentation-v3` (V3, `web/presentation.py`). Neither accepts `fx_conversion`; V2 and V3 refuse drafts that contain one.

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
| account_transfer (*v0.7, proposed*) | Move money | From (`from_account_label`) · To (`to_account_label`) · Amount (`amount` + `currency`) |
| bill_payment | Pay bill | Biller (`biller_label`) · Reference (`bill_reference`) · Amount · From |
| fx_conversion | Convert currency | From (`from_account_label`) · Sell (`sell_amount` + `sell_currency`) · Rate ("1 SGD = 0.740000 USD", with `rate_as_of`) · Get (`buy_amount` + `buy_currency`) · Into (`to_account_label`) |
| stock_order | Buy shares / Sell shares | Stock (`ticker` · `company_name`) · Quantity · Order type (market / limit + `limit_price`) · Price now (from `/api/quote`, marked "estimate") · Estimated total (quantity × price, marked "estimate") · Pay from / Pay into |

Other states the UI must show: **preview** (empty card of the guessed type while typing, from `/api/preview`), **thinking** (waiting for `/api/understand`), **question** (text + one chip per option + "None of these"), **answer** (*v0.6, proposed*: a read-only card listing the rows of the answer's kind, such as accounts and balances, transactions or bills, with no confirm button), **blocked**, **unsupported**, **frozen** (mismatch, listing each field), **needs extra check** (code entry), **executed** (references), **rejected** (plain-English reason).

## 6. Errors

Malformed requests return HTTP 400 with `{"error": {"code": "bad_request", "message": "…"}}`. Unknown user or session returns 404 with code `not_found`. Upstream failures (Jev, LLM, ASR down) return 502 with code `upstream_error`; a missing server key for a model returns 503 with the same code. A body over the size cap (256 KB of JSON, 2 MB of audio) returns 413 `too_large`. Too many calls from one client IP in a rate group, or too many model-backed calls (`/api/understand`, `/api/transcribe`, `/api/presentation-*`; `/api/preview` has its own budget) from everyone together, return 429 `rate_limited` with a `Retry-After` header; the UI waits and lets the user try again. Groups per IP per minute: model 40, preview 120, validate 120, write 60 (`/api/validation/cancel`, `/api/challenge`, `/api/execute`, `/api/device/register`, `/api/audit/presentation`), read 120 (every other `/api/` path). `/api/understand` refuses a `user_id` not shaped `u_…` or a `session_id` not shaped `s_…` (400) and an unknown user (404) before touching any state. Business outcomes such as `rejected` or `mismatch` return HTTP 200 with the status shown above.

## 7. Stubs

Until a real endpoint works, its router returns the example responses in this file unchanged. Darren builds the UI against the stubs from day one.

## 8. Audit events

Intent side (`actor: "intent"`). Jayanth adds the validation and bank events here. `data` never holds keys or secrets. `/api/preview` calls are not logged, nor kept as evidence: they are display only and too frequent.

The audit chain (`app/audit.py`) accepts only a flat allowlist of non-sensitive fields, so intent events carry a `reason`, a `count` and sometimes a `draft_id`; transcripts, Jev's probabilities and the options offered stay in the encrypted evidence store (v0.8), linked by `session_id` there. `GET /api/audit` is public, so intent events never carry `session_id` (v0.10). A failed audit write stops the request (503).

| `event` | `data` |
|---|---|
| `shield_checked` | `reason` (`flagged` or `clear`) |
| `gate_decision` | `reason` (`proceed` or `ask`) |
| `question_asked` | `reason` (the question asked, e.g. `recipient`), `count` (its number) |
| `answer_received` | `reason` (`chip` or `speech`) |
| `draft_built` | `draft_id`, `count` (actions) |
| `answer_given` (*v0.6, proposed*) | `reason` (the kind) |
| `blocked` | `reason` |
| `cancelled` | `reason` |
| `transcribed` | `reason` (`ok` or `empty`), `count` (words) |

Jev's answers per question are in the evidence store, as the stand-in text Jev saw, never the transcript (D-026). Jayanth adds `fx_converted` (actor `bank`) with `draft_id`, `sell_amount`, `sell_currency`, `buy_amount`, `buy_currency`, `rate`.

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
| `/api/presentation-v4/evaluate` | A draft arrives, or the objective changes | Two calls in sequence (§3.4); on error use the fixed layout |
| **Cancel** | Any time | Abort the call in flight and drop the `session_id`; the next command gets a new one |

Voice: if the browser's built-in speech recognition gives live text, the UI may send it to `/api/preview` while the user speaks. Only the Tencent ASR transcript goes to `/api/understand`.

## 10. Changes in v0.4

- **Spoken answers:** while a question is open, a `transcript` in the same session answers it (§3.1).
- **Sums of money:** "invest 2k in Apple" becomes a `stock_order` with a quantity computed by code (§2.1).
- **"The rest":** means what is left after the actions said before it (§3.1).
- **Currency conversion:** new `fx_conversion` action (§2.1), `GET /api/fx` and `get_fx_rate` (§3.3), and reject reason `rate_changed`. The intent side, the rates and `/api/fx` are built; the ledger and rules for it are pending.
- **Amounts are SGD by default** unless the user names another currency (§1).
- **Already live (D-026):** Jev never sees real amounts. Every number the user says is swapped for a stand-in, and balances never appear in Jev's options (§3.1, §8). Share counts and limit prices are only numbers the user said, never a fraction of a balance.
- **Testing:** `scripts/chat.py` runs a text conversation with `/api/understand` against live Jev, turn by turn (`.venv/bin/python scripts/chat.py -v`).

## 11. Changes in v0.5

- **Presentation endpoints documented (§3.4).** They were already in use by V2 to V5; this records them. `action_types` now includes `fx_conversion` for V4 and V5.
- **503:** a missing model key returns 503 `upstream_error` (§6).

## Read-only enquiry handoff (13 October 2026)

Live read-only requests use Winston's `status: "answer"` contract below. The former
`status: "enquiry"` route is retired. Broad insights remain a separate gallery capability.

POST /api/presentation-v5/answer accepts structural metadata only: kind (one of the
 eight answer kinds), count (0..100000), timeline_requested (boolean, default false).
It returns source (jev/fallback), variant (list/cards/timeline), detail (compact/expanded),
summary (boolean), latency_ms. It never fetches bank data or returns financial values.
Cards are eligible for balance/holdings/quote/payees with multiple records; timeline
only for nonempty bills; totals only for balance/holdings, separately per currency.
The frontend retains all upstream records and ordering except chronological bill timelines.
A complete fallback renders immediately. Unknown kinds fail closed; stale presentation
responses cannot replace a newer answer. No signing controls appear on answers.

POST /api/presentation-v5/enquiry accepts user_id and topic. It reads synthetic bank
insights and returns data, a bounded spec (root and allowlisted elements), source,
topic and latency_ms. Required topic components cannot be omitted. JEV chooses layout,
detail and eligible optional support using structural context only. Gateway failure
returns an explicitly labelled fallback. GET /api/account-insights?user_id=u_demo
returns the same data without a model call, for the gallery.

Data: allowlisted accounts and completed history, next-30-day cashflow forecasts,
and per-currency previous-calendar-month/current-month-to-date outflows. Dates use
Asia/Singapore. The history is a seed sample, not a complete statement; missing categories
remain Uncategorised. Missing history/schedules are unavailable, never fabricated zeros.
User-ID selection follows the existing demo API; production authentication is not added.
These endpoints do not validate, register devices, sign or execute transactions.
## 12. Changes in v0.6 (proposed)

Needs Darren's and Jayanth's agreement before it is final.
- **Answers:** a new `/api/understand` reply, `status: "answer"`, for questions that move no money, in eight kinds: `balance`, `transactions`, `bills`, `holdings`, `quote`, `fx_rate`, `payees`, `help` (§3.1). Bank side: `readonly.list_transactions` and `readonly.list_bills`. UI side: one **answer** card (§5).
- **Audit:** new intent event `answer_given` (§8).

## 13. Changes in v0.7 (proposed)

Needs Darren's and Jayanth's agreement before it is final.
- **Own-account moves:** new action `account_transfer` (§2.1) and card **Move money** (§5). Between currencies, a standalone `fx_conversion`, which also covers "convert 1,000 SGD to USD".
- **Bank:** the ledger applies `account_transfer` like a transfer whose payee is the user's own account; D-007 decides limits.


## Independent semantic auditing (14 October 2026)

`POST /api/validate` still accepts `{draft}`. Only an exact server-registered current
Winston draft is accepted. The server uses its saved original instruction, explicit
clarification selections, follow-ups and bank snapshot; client transcript replacement
fails closed. Match returns `{status:"match",receipt}`. Mismatch and uncertain return
`{status:"mismatch"|"uncertain",fields:[{field,draft_value,transcript_says,note}]}`.
Pending, unavailable and stale return `{status,message}`. Unavailable may be retried;
a completed mismatch/uncertain result is terminal until a new instruction/revision.
Every action field and action coverage must be audited. Amounts remain decimal strings.

`POST /api/validation/cancel` accepts `{draft}` and returns `{status:"cancelled"}`.
New intent turns supersede outstanding approval. Read-only answers never invoke auditing.
JEV layout changes do not alter the audited draft. Local edited payloads require a fresh
Winston handoff; the server refuses to validate fabricated client drafts.

The shared bounded in-memory store supports ONE server worker only. It expires drafts
and their receipts after five minutes; restart revokes all approvals. Receipts bind a
random revision and full canonical draft digest using an HMAC secret. Setting a persistent
secret does not make approvals survive restart. This is demo user-ID ownership, not
production authentication. No new authenticated enrollment is claimed.

Challenge now verifies the current receipt, stores an expiring nonce and returns the
exact canonical draft payload. Execution checks that the receipt remains current but
FAILS CLOSED with 503 until Jayanth's signature verification, risk rules and atomic
ledger are connected. It no longer returns an unconditional fake success. No funds move.

Local Codex is the temporary default validator (`VALIDATOR_PROVIDER=codex`), using an
already signed-in CLI with read-only sandbox, no shell/web/apps, ignored user config,
an isolated temporary working directory and ephemeral execution. Requires a current
Codex CLI on PATH on Windows or macOS. It uses its default model unless
`VALIDATOR_CODEX_MODEL` is set. CLI timeout is 45 seconds, frontend deadline 60 seconds.
Alternatively set `VALIDATOR_PROVIDER=compatible-api`, `LLM_BASE_URL` (including /v1),
`LLM_API_KEY`, `VALIDATOR_MODEL` (distinct from JEV and rewriter); deadline 12 seconds.
Malformed/missing model output, missing config, audit-log failure and timeouts never
produce approval. Raw audit context goes to the chosen model provider; performance
logs contain only draft ID, verdict and elapsed milliseconds (count field).


## Persistent audit evidence (v0.8)

The existing `/api/audit` and `var/audit.jsonl` remain compatible legacy event summaries.
The detailed request timeline is stored atomically with encrypted evidence in ignored
`var/evidence.sqlite3`. This is a separate hash chain; historical JSONL records are not
backfilled and have no recoverable detailed evidence. Logging never authorizes a transaction.

Each HTTP operation has a server-generated `req_` ID, returned as `X-Audit-Request-ID`.
Client session IDs and `X-Audit-Session` are correlation claims, not authentication.
Server draft events additionally bind a server revision and canonical draft SHA-256 digest.
Metadata contains event/time/IDs, evidence digest, expiry and previous hash.
Timeline time/expiry use Unix seconds (the legacy audit retains ISO timestamps). Evidence includes
original inputs, clarification responses, bank snapshots, model requests/responses, validator
policy digest, and request outcomes. Model identities are recorded where provided; Local
Codex's unset model is explicitly `cli_default`, not an invented resolved model version.
No chain-of-thought is requested or stored.

`GET /api/audit/timeline?session_id=s_...&limit=100&include_evidence=true` requires
`Authorization: Bearer <AUDIT_ADMIN_TOKEN>`. Limit is 1?500, session filter optional;
metadata-only by default. Responses have `Cache-Control: no-store`. Returns
`{chain_valid:true, entries:[{position,event_id,event,time,expires_at,request_id?,session_id?,
draft_id?,revision?,draft_digest?,evidence_digest,previous_hash,hash,evidence_state,
evidence?}]}`. Corruption returns 503, never fabricated empty history. Expired evidence
has `evidence_state:expired` and no evidence body. A valid chain does not prove the model
was correct or that an event writer was honest. The running server remembers the chain's
head, so rows deleted from the end are caught until it restarts; a store found tampered
refuses new writes (503) until then. Independent checkpoints that survive a restart are
not implemented.

`POST /api/audit/presentation` accepts
`{session_id, draft_id?, manifest:object (at most 16 KB as JSON), source:jev|fallback}` and returns
`{status:recorded}`. V5 submits the final component spec before enabling confirmation;
read-only answers report initial fallback and selected presentation on a best-effort basis.
These are explicitly `presentation_client_reported`, not proof of rendered pixels, user
consent, or server-validated component choices. The server's JEV exchange is separate evidence.

Encryption uses Fernet (cryptography 46.0.7). Required persistent `AUDIT_EVIDENCE_KEY` and
independent `AUDIT_ADMIN_TOKEN` belong in local `.env` or deployment secret storage, never
frontend/source control. Losing the encryption key loses evidence access; keep it across
restarts. Encryption protects the database alone, not a machine compromised alongside its
key. Default evidence retention is seven days (`AUDIT_RETENTION_DAYS`), removed on writes,
admin reads, startup and hourly maintenance while running. Metadata remains. Backups and
cloud-synced historical copies need their own retention policy. To rotate, set
`AUDIT_EVIDENCE_KEY=new,old`: the newest key encrypts and signs, older ones still verify and
decrypt. Keep a retired key listed while rows it signed remain. No automatic rotation.

Structured secret fields (OTP, authorization, cookies, credentials, receipts, private-key
material, PIN, CVV, card number, NRIC, signature, nonce) and configured secret values are
redacted before encryption. Passwords, PINs and OTPs typed after their keyword, card numbers,
NRICs, API keys and JWTs are replaced in free text, and in transcripts before Jev or the
validator see them (§14). Raw HTTP headers,
raw audio and malformed unparsed bodies are not stored. Free text remains sensitive;
redaction is not a guarantee of discovering every arbitrary secret a user may type.
Transcription is currently a labelled stub event. Evidence over 64 KB per item keeps only its size and SHA-256; past 500 MB in total, new events keep metadata only (`evidence_state: "capacity"`). The chain hash is an HMAC keyed from `AUDIT_EVIDENCE_KEY`, so rewriting history needs the key; writes check the newest link and reads check the whole chain. `evidence_digest` and `draft_digest` in metadata are HMACs too, so the file alone can't confirm a guessed request. The database is owner-only (0600).
Missing encryption or storage failure stops audited requests; expired/cancelled/stale drafts
cannot regain approval through audit replay. Confirmation attempts and unavailable bank
execution are recorded, but no signature verification or ledger execution is implied.
SQLite is local storage with serialized writes; this remains a demo architecture, not a
production distributed audit service. Windows was exercised; macOS uses the same SQLite/
Fernet code but has not been runtime-tested here.


## 13. Intent-aware presentation and conditional plans (v0.9)

`/api/understand` may return `status: "plan"`, with `plan` and the usual `meta`.
A plan is display-only: it has no draft ID, cannot be validated or signed, and is
never registered in authoritative transaction state. A new turn still revokes the
previous draft. A plan creates no payment or monitoring job. Resolved immediate conditions may instead return a signed-condition draft, as described below.

`plan` contains:
- `request`: the original instruction (or the latest conditional follow-up).
- `condition`: `text`, nullable `ticker`, `operator` (`lt/lte/gt/gte/eq/ne`), `threshold`
  (money string), `currency`, `quote` (the existing QuoteResponse), and `state`
  (`met/not_met/stale/unknown`). Only one fully parsed stock-price comparison is
  evaluated in this slice. Unknown and compound conditions retain their text.
- `actions`: zero to four existing Action objects, for display only.
- `timing`: `now/watch/unspecified`. Winston asks the existing question/answer loop
  when a resolved action has no explicit timing. Recipient ambiguity is resolved
  before presentation. Unbuildable portions are listed in `unresolved`.
- `executable`: always false; `limitation`: code-owned explanation.
- `intent`: the privacy-filtered PresentationIntent described below.

Threshold numbers are extracted separately from action amounts. Existing currency
rules apply: unqualified dollars mean SGD, even for a USD-priced security. No FX
conversion is inferred for a comparison. A mismatched currency prompts for clarification before an executable draft can be prepared.
Quotes older than CONDITION_QUOTE_MAX_AGE_SECONDS (60 seconds), or from the future,
produce `stale`. Comparison status is presentation evidence, never authorization.

Draft and read-only answer responses add nullable `presentation`:
`{request, conversation, tasks, constraints}`. This preserves sanitized wording,
relevant turns and task types; numbers use stand-ins and known payee/account/biller
identities are replaced with references. Exact financial values stay in the task
and bank records. These fields carry user data, not instructions to obey.

`POST /api/presentation-v5/plan` accepts `{plan, viewport: "mobile"|"desktop"}`.
It returns `{source: "jev"|"fallback", layout: "stacked"|"flow", nodes}`. Each node
has `component`, `binding` and `variant`. Allowed nodes are Condition, QuoteMetric,
Relationship, PlannedAction and AvailabilityNotice. The condition, relationship,
each action exactly once, and availability notice are mandatory and ordered.
Only QuoteMetric may be omitted. Bindings refer to `condition`, a zero-based action
index, or `plan`; they cannot carry invented values. The client checks coverage and
variants independently and retains the complete fallback when choices fail.

The composer receives sanitized intent, structural facts, timestamp validity,
viewport, eligible components and server-owned bank capability hints. One model
call chooses variants and layout; code binds actual values and enforces coverage.
Existing V4 evaluation context and V5 answer requests accept optional `intent`.
Capability hints never authorize payment: scheduled payment and price monitoring remain unavailable. Immediate quote conditions require the checks below. Single-SGD-transfer execution still needs
validation, signature and all bank checks.

This extends presentation vocabulary; it does not enable arbitrary banking APIs,
generate UI code, or claim every natural-language condition can be evaluated.


### Request-aware enquiry composition

`POST /api/presentation-v5/enquiry` also accepts `topic: "request"` with `request`
(max 2000 characters), `conversation` (max 20 strings), optional `winston`
(PresentationIntent), and `outcome` (draft, answer, plan, unsupported).
The server masks the request/history using known account and recipient identities.
JEV chooses from available read-only components across topics, including timestamped
QuoteList data. Financial values bind to bank reads, never model-written values.
The response uses the existing enquiry tree with optional `data.quotes`.
There is no coverage verdict or payment gate from UI composition.

V5 embeds this request as optional `context_request` in its existing
`/presentation-v4/evaluate`, `/presentation-v5/answer`, or `/presentation-v5/plan`
call. The server appends prefixed component choices to the same native JEV request
and returns the validated context tree as optional `context_view`. No extra model
call is added. Multi-phase V4 composition includes context selection in its first
phase only. Unsupported Winston responses use the enquiry endpoint as their only
UI composer; blocked and clarification responses do not enter it.

The original request and conversation are masked on the server before JEV sees
them. Original financial records remain bound locally. Composition failures use
the existing fallback. Validation, signature and bank checks remain authoritative;
conditional plans retain their existing non-executable status.


### Immediate quote conditions (v0.12)

A resolved `Draft` may include `condition: {ticker, operator, threshold, currency}`.
Operators are `lt/lte/gt/gte/eq/ne`; threshold is a decimal money string. The field
is omitted entirely on ordinary drafts, preserving their existing signed encoding.
Winston selects the asset, comparison and numeric candidate in its existing native
request. Deterministic code compares a matching, fresh bank quote using Decimal.
Only a met, single comparison with resolved actions and immediate timing becomes
a draft. Unsupported/compound conditions, stale data and unmet comparisons remain
non-executable plans. This does not create a watcher or scheduled payment.

The validator audits the predicate against the instruction and supplied data.
The predicate is covered by the receipt, canonical draft and customer signature.
The gateway reads and checks the quote before issuing a challenge and again before
new execution. A failed check rejects the request without debiting. Exact committed
retries return the original receipt without reevaluating or repeating the payment.
The UI displays the condition and quote timestamp, polls the quote, and disables
confirmation when it is no longer met; backend enforcement remains authoritative.

Stored bank snapshots are not a live market feed. Stale snapshots need replacement
by a genuine fresh data source; their timestamps must not be refreshed artificially.


### Demo condition policy (supersedes v0.12 freshness and recheck rules)

The virtual bank's stored quote is authoritative for this demo regardless of age.
Winston evaluates the resolved predicate once while preparing the draft. Missing
or wrong-currency quotes and false conditions cannot prepare a conditional draft.
The predicate remains signed and its meaning is independently validated, but its
price is not polled or re-evaluated by validation, challenge or execution. The UI
says "Condition met when prepared". Confirmation remains a separate user action;
normal bank limits, signature checks and idempotency remain enforced. This is a
preparation-time demo condition, not a live-market conditional order.


### Structured-first presentation (v0.13)

PresentationIntent adds `coverage: complete|partial` (default complete) and `gaps`
(default empty). Winston asks a capability-coverage question in its existing model
batch, then checks which confidently selected tasks/information are absent from the
actual output. A specific gap enables broader composition; weak coverage evidence
alone does not trigger clarification or reinterpretation. This is presentation
metadata, never permission to execute or a replacement for the independent validator.

Complete outputs use structured tasks and banking context. Their original prompt and
history are omitted from the UI model state. Partial outputs with nonempty gaps may
include a masked `context_request` in the existing UI request. The server enforces
this gate too. Unsupported results retain a read-only enquiry fallback. Question
responses remain in Winston's existing clarification loop; no UI composition starts
until that loop returns a result. No extra model round trip is introduced. Existing
card deduplication and the two-supplementary-card limit remain in force.


### Integration evidence (0.14)

The connected `/api/validate` continues to accept only server-registered draft revisions and
returns the existing `match`, `mismatch`, `uncertain`, `unavailable`, `pending` or `stale` result.
It uses the configured `llm.audit` provider and `transaction_state` receipt/lease authority.
Independent bank reference checks additionally verify labels, ownership references, currencies,
bill references, quote identities and FX arithmetic. No extra model request is introduced.

Validator snapshots include the selected biller's stored `bill_reference` and SGD currency,
explicit SGD/market/null-limit defaults, and available account metadata without unused balances.
Intent-selected `amounts_said` is retained for audit but excluded from independent model input.
Explicit customer instructions override defaults; ambiguous recipients still require clarification.
Jayanth's source-offset comparison and regression summaries remain an isolated internal interface;
they do not replace the active semantic model schema or publish receipts through the public route.

The operator-only `sync-catalogue` command adds absent seed payees to existing runtime users with
an idempotent `catalogue_updated` audit event. It preserves balances, holdings, bill settlement,
transaction history, payment controls and existing records. New seed positions apply only to fresh
installations. Eight quotes and sample history are read from the seed; prices remain demo data.

## 14. Integrated security audit changes (v0.15, 16 October 2026)

- **Conversations belong to a browser.** Every `/api/` response without a valid `dcta_client` cookie sets one (HttpOnly, `SameSite=Strict`, `Path=/api`, `Secure` over HTTPS). A `session_id` started by one browser is refused for every other: `/api/understand` returns 404 `not_found`, `/api/validate` returns `stale`, `/api/challenge` 409, and `/api/validation/cancel` changes nothing. Browsers send the cookie on same-origin requests without any UI change; other clients must keep cookies (httpx and the test client do).
- **§8:** intent events no longer carry `session_id`, and previews are no longer kept as evidence.
- **§6:** every `/api/` path has a per-IP rate group; previews and model calls have separate global budgets. The audit middleware's 413 and 503 bodies carry `too_large` and `upstream_error`. Every `/api/` response has `Cache-Control: no-store`.
- **`/api/validate`:** an `unavailable` result is returned again for 10 seconds before the validator runs once more, and a check that crashed stops reading as `pending` after its deadline.
- **`/api/audit/presentation`:** `manifest` is at most 16 KB as JSON (400 otherwise).
- **`draft_id`** is `d_` and 16 hex characters (was 6).

Browser ownership and validation retry timestamps persist in the encrypted approval store when
local bank execution is enabled. Persistence uses wall-clock timestamps on Windows and macOS.
Existing approvals without a browser owner require a fresh request; committed transactions and
device registrations remain intact. Evidence format 2 requires the explicit operator migration
in README for previous stores. Payload/draft digests verify against retained rotation keys;
idempotent bank commits retain stable event IDs. Another process may append a verified extension
of the remembered audit head; truncation or replacement of that prefix fails closed.
