import { z } from "zod";

const money = z.string().regex(/^\d+\.\d{2}$/);
const currency = z.string().regex(/^[A-Z]{3}$/);
const probability = z.number().min(0).max(1);
const common = { from_account_id: z.string(), from_account_label: z.string(), currency };
export const actionSchema = z.discriminatedUnion("type", [
  z.strictObject({
    ...common,
    type: z.literal("transfer"),
    to_payee_id: z.string(),
    to_payee_label: z.string(),
    amount: money,
  }),
  z.strictObject({
    ...common,
    type: z.literal("bill_payment"),
    biller_id: z.string(),
    biller_label: z.string(),
    bill_reference: z.string(),
    amount: money,
  }),
  z
    .strictObject({
      ...common,
      type: z.literal("stock_order"),
      side: z.enum(["buy", "sell"]),
      ticker: z.string(),
      company_name: z.string(),
      quantity: z.number().int().positive(),
      order_type: z.enum(["market", "limit"]),
      limit_price: money.nullable(),
    })
    .refine((a) => (a.order_type === "limit") === (a.limit_price !== null), "Invalid limit price"),
  // Money between two of the user's own accounts in one currency (CONTRACTS §2.1, v0.7 proposed).
  z.strictObject({
    ...common,
    type: z.literal("account_transfer"),
    to_account_id: z.string(),
    to_account_label: z.string(),
    amount: money,
  }),
  // Code adds a conversion before a purchase in another currency; the user never edits it (CONTRACTS §2.1).
  z.strictObject({
    type: z.literal("fx_conversion"),
    from_account_id: z.string(),
    from_account_label: z.string(),
    to_account_id: z.string(),
    to_account_label: z.string(),
    sell_amount: money,
    sell_currency: currency,
    buy_amount: money,
    buy_currency: currency,
    rate: z.string().regex(/^\d+\.\d{6}$/),
    rate_as_of: z.iso.datetime({ offset: true }),
  }),
]);
export const metaSchema = z.strictObject({
  latency_ms: z.number().int().nonnegative(),
  model: z.string(),
  source: z.enum(["jev", "fallback", "fake", "none"]),
  phrasings: z.number().int().nonnegative(),
  cached: z.boolean(),
});
const evidence = z.strictObject({
  question: z.string(),
  pick: z.string(),
  avg_p: probability,
  spread: probability,
  probabilities: z.record(z.string(), probability),
});
export const objectiveSchema = z.enum([
  "prepare",
  "assess_affordability",
  "simulate",
  "check_recipient",
  "review",
]);
// Reject unknown draft fields rather than silently dropping part of a transaction.
export const quoteConditionSchema = z.strictObject({
  ticker: z.string().min(1).max(16), operator: z.enum(["lt", "lte", "gt", "gte", "eq", "ne"]),
  threshold: money, currency,
});
export const draftSchema = z.strictObject({
  condition: quoteConditionSchema.optional(),
  draft_id: z.string(),
  user_id: z.string(),
  session_id: z.string(),
  transcript: z.string(),
  created_at: z.iso.datetime({ offset: true }),
  actions: z.array(actionSchema).min(1).max(4),
  evidence: z.array(evidence),
});
export const presentationIntentSchema = z.strictObject({
  request: z.string().max(2000), conversation: z.array(z.string()).max(20),
  tasks: z.array(z.string()).max(4), constraints: z.array(z.string()).max(8),
  coverage: z.enum(["complete", "partial"]).default("complete"),
  gaps: z.array(z.string()).max(8).default([]),
});
export const requestPlanSchema = z.strictObject({
  request: z.string().max(500),
  condition: z.strictObject({
    text: z.string().max(500), ticker: z.string().nullable(),
    operator: z.enum(["lt", "lte", "gt", "gte", "eq", "ne"]).nullable(),
    threshold: money.nullable(), currency: currency.nullable(),
    quote: z.strictObject({ticker: z.string(), company_name: z.string(), price: money,
      currency, as_of: z.iso.datetime({offset: true})}).nullable(),
    state: z.enum(["met", "not_met", "stale", "unknown"]),
  }),
  actions: z.array(actionSchema).max(4), timing: z.enum(["now", "watch", "unspecified"]),
  unresolved: z.array(z.string()).max(8), executable: z.literal(false),
  limitation: z.string(), intent: presentationIntentSchema,
});
export type RequestPlan = z.infer<typeof requestPlanSchema>;

export const understandSchema = z.discriminatedUnion("status", [
  z.strictObject({status: z.literal("plan"), plan: requestPlanSchema, meta: metaSchema}),
  z.strictObject({
    status: z.literal("draft"),
    presentation: presentationIntentSchema.nullable().optional(),
    draft: draftSchema,
    objective: objectiveSchema,
    // Hypothetical values for `simulate`, by action index; never validated, signed or executed.
    scenario: z.record(z.string(), z.partialRecord(z.enum(["amount", "quantity"]), z.string())).nullable(),
    meta: metaSchema,
  }),
  z.strictObject({
    status: z.literal("question"),
    question: z.strictObject({
      question_id: z.string(),
      text: z.string(),
      options: z.array(z.strictObject({ option_id: z.string(), label: z.string() })),
      allow_none: z.boolean(),
    }),
    evidence: z.array(evidence),
    meta: metaSchema,
  }),
  z.strictObject({
    status: z.literal("blocked"),
    reason: z.literal("possible_injection"),
    message: z.string(),
    meta: metaSchema,
  }),
  // v0.6 answers (proposed): any kind passes here; answerSchema checks the kinds this UI knows.
  z.looseObject({ status: z.literal("answer"), kind: z.string(), meta: metaSchema }),
  z.strictObject({ status: z.literal("unsupported"), message: z.string(), meta: metaSchema }),
  z.strictObject({
    status: z.literal("cancelled"),
    reason: z.enum(["user_declined", "too_many_questions"]),
    message: z.string(),
    meta: metaSchema,
  }),
]);
export const previewSchema = z.strictObject({
  intent: z.strictObject({
    value: z.enum(["transfer", "bill_payment", "stock_order", "none"]),
    confidence: probability,
    probabilities: z.record(z.string(), probability),
  }),
  meta: metaSchema,
});
export const transcribeSchema = z.strictObject({
  transcript: z.string(),
  confidence: probability.nullable(),
});
export const accountsSchema = z.strictObject({
  accounts: z.array(
    z.strictObject({ account_id: z.string(), label: z.string(), type: z.string(), currency, balance: money }),
  ),
  payees: z.array(z.strictObject({ payee_id: z.string(), name: z.string(), label: z.string() })),
  billers: z.array(
    z.strictObject({ biller_id: z.string(), name: z.string(), label: z.string(), amount_due: money }),
  ),
  holdings: z.array(z.strictObject({ ticker: z.string(), quantity: z.number().int().nonnegative() })),
});
export const quoteSchema = z.strictObject({
  ticker: z.string(),
  company_name: z.string(),
  price: money,
  currency,
  as_of: z.iso.datetime({ offset: true }),
});
const validationFields = z.array(z.strictObject({
  field: z.string(), draft_value: z.string(), transcript_says: z.string(), note: z.string(),
}));
export const validationSchema = z.discriminatedUnion("status", [
  z.strictObject({status: z.literal("match"), receipt: z.string().min(1)}),
  z.strictObject({status: z.literal("mismatch"), fields: validationFields}),
  z.strictObject({status: z.literal("uncertain"), fields: validationFields}),
  z.strictObject({status: z.enum(["pending", "unavailable", "stale"]), message: z.string()}),
]);

export type Action = z.infer<typeof actionSchema>;
export type Draft = z.infer<typeof draftSchema>;
export type Objective = z.infer<typeof objectiveSchema>;
export type Understand = z.infer<typeof understandSchema>;
export type Accounts = z.infer<typeof accountsSchema>;
export type Quote = z.infer<typeof quoteSchema>;
export type Validation = z.infer<typeof validationSchema>;
export type Answer = z.infer<typeof answerSchema>;

const answer = { status: z.literal("answer"), meta: metaSchema, presentation: presentationIntentSchema.nullable().optional() };
const time = z.iso.datetime({ offset: true });
// CONTRACTS §3.1 answers (v0.6, proposed): read-only replies; nothing here can be signed.
export const answerSchema = z.discriminatedUnion("kind", [
  z.strictObject({ ...answer, kind: z.literal("balance"), accounts: accountsSchema.shape.accounts }),
  z.strictObject({
    ...answer,
    kind: z.literal("transactions"),
    transactions: z.array(
      z.strictObject({
        transaction_id: z.string(),
        time,
        account_id: z.string(),
        account_label: z.string(),
        description: z.string(),
        direction: z.enum(["credit", "debit"]),
        amount: money,
        currency,
        payee_id: z.string().nullable(),
      }),
    ),
  }),
  z.strictObject({
    ...answer,
    kind: z.literal("bills"),
    bills: z.array(
      z.strictObject({
        label: z.string(),
        amount: money,
        currency,
        due_at: time,
        account_id: z.string(),
        account_label: z.string(),
        recurrence: z.enum(["weekly", "monthly", "yearly"]).nullable(),
      }),
    ),
  }),
  z.strictObject({
    ...answer,
    kind: z.literal("holdings"),
    holdings: z.array(
      z.strictObject({
        ticker: z.string(),
        company_name: z.string(),
        quantity: z.number().int().nonnegative(),
        price: money,
        currency,
        value: money,
        as_of: time,
      }),
    ),
  }),
  z.strictObject({ ...answer, kind: z.literal("quote"), quotes: z.array(quoteSchema) }),
  z.strictObject({
    ...answer,
    kind: z.literal("fx_rate"),
    rate: z.strictObject({
      from: currency,
      to: currency,
      rate: z.string().regex(/^\d+\.\d{6}$/),
      as_of: time,
    }),
  }),
  z.strictObject({ ...answer, kind: z.literal("payees"), payees: accountsSchema.shape.payees }),
  z.strictObject({ ...answer, kind: z.literal("help"), message: z.string() }),
]);
