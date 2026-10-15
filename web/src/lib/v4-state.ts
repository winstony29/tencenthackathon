import { financialSchema } from "./v4-financial";
import { z } from "zod";
import { accountsSchema, draftSchema, quoteSchema, presentationIntentSchema, type Draft } from "./contracts";
import { editedActions, updateEdit, type DraftEdits } from "./draft-editor";
import { fromCurrency } from "./money";

export const objectiveSchema = z.enum(["prepare", "assess_affordability", "simulate", "review"]);
export type Objective = z.infer<typeof objectiveSchema>;
export const handoffSchema = z.strictObject({
  draft: draftSchema,
  intent: presentationIntentSchema.nullable().optional(),
  objective: objectiveSchema,
  data: z.strictObject({
    accounts: accountsSchema,
    quotes: z.record(z.string(), quoteSchema),
    source: z.enum(["mock", "supplied"]),
    financial: financialSchema.optional(),
  }),
  scenario: z.record(z.string(), z.record(z.string(), z.string())).optional(),
});
export type Handoff = z.infer<typeof handoffSchema>;
export type State = {
  handoff: Handoff | null;
  edits: DraftEdits;
  scenario: DraftEdits;
  approved: Record<number, boolean>;
  done: boolean;
  error: string;
};
export const initialState: State = {
  handoff: null,
  edits: {},
  scenario: {},
  approved: {},
  done: false,
  error: "",
};
export type Event =
  | { type: "handoff"; value: unknown }
  | { type: "edit" | "scenario"; index: number; field: string; value: string }
  | { type: "objective"; value: Objective }
  | { type: "apply" | "finish" | "reset" }
  | { type: "approve"; index: number };
export function effective(state: State, hypothetical = false) {
  if (!state.handoff) return { actions: [], errors: {} as Record<number, string> };
  const edits = structuredClone(state.edits);
  if (hypothetical)
    for (const [i, fields] of Object.entries(state.scenario)) edits[+i] = { ...edits[+i], ...fields };
  return editedActions(state.handoff.draft, edits, state.handoff.data.accounts);
}
function validHandoff(h: Handoff) {
  const { accounts, quotes, financial } = h.data;
  if (
    financial?.accounts.some(
      (c) =>
        !accounts.accounts.some((a) => a.account_id === c.account_id) ||
        c.commitments?.bills.some((b) => {
          if (b.covered_by_draft_action === undefined) return false;
          const a = h.draft.actions[b.covered_by_draft_action];
          return a?.type !== "bill_payment" || a.from_account_id !== c.account_id || a.amount !== b.amount;
        }),
    )
  )
    return false;
  if (
    new Set(accounts.accounts.map((a) => a.account_id)).size !== accounts.accounts.length ||
    new Set(accounts.payees.map((a) => a.payee_id)).size !== accounts.payees.length ||
    new Set(accounts.billers.map((a) => a.biller_id)).size !== accounts.billers.length
  )
    return false;
  if (Object.entries(quotes).some(([key, q]) => key !== q.ticker)) return false;
  return (
    !Object.keys(editedActions(h.draft, {}, accounts).errors).length &&
    h.draft.actions.every(
      (a) =>
        accounts.accounts.some(
          (b) =>
            b.account_id === a.from_account_id &&
            b.currency === fromCurrency(a) &&
            b.label === a.from_account_label,
        ) &&
        (a.type !== "fx_conversion" ||
          accounts.accounts.some(
            (b) =>
              b.account_id === a.to_account_id &&
              b.currency === a.buy_currency &&
              b.label === a.to_account_label,
          )) &&
        (a.type !== "account_transfer" ||
          (a.to_account_id !== a.from_account_id &&
            accounts.accounts.some(
              (b) =>
                b.account_id === a.to_account_id &&
                b.currency === a.currency &&
                b.label === a.to_account_label,
            ))) &&
        (a.type !== "transfer" ||
          accounts.payees.some((p) => p.payee_id === a.to_payee_id && p.label === a.to_payee_label)) &&
        (a.type !== "bill_payment" ||
          accounts.billers.some((b) => b.biller_id === a.biller_id && b.label === a.biller_label)),
    )
  );
}
function preserveEdits(state: State, draft: Draft): DraftEdits {
  const edits: DraftEdits = {};
  if (state.handoff?.draft.draft_id !== draft.draft_id) return edits;
  draft.actions.forEach((a, i) => {
    const old = state.handoff!.draft.actions[i];
    if (!old || old.type !== a.type) return;
    const identity =
      a.type === "transfer"
        ? "to_payee_id"
        : a.type === "stock_order"
          ? "ticker"
          : a.type === "fx_conversion" || a.type === "account_transfer"
            ? "to_account_id"
            : "biller_id";
    const before = old as unknown as Record<string, unknown>,
      after = a as unknown as Record<string, unknown>;
    if (before[identity] !== after[identity]) return;
    for (const [field, value] of Object.entries(state.edits[i] ?? {}))
      if (before[field] === after[field]) edits[i] = { ...edits[i], [field]: value };
  });
  return edits;
}
export function reducer(state: State, event: Event): State {
  if (event.type === "reset") return initialState;
  if (event.type === "handoff") {
    const parsed = handoffSchema.safeParse(event.value);
    if (!parsed.success || !validHandoff(parsed.data))
      return {
        ...state,
        error: "Waiting for a complete, consistent task from Winston.",
      };
    const h = parsed.data;
    const next: State = {
      handoff: h,
      edits: preserveEdits(state, h.draft),
      scenario: {},
      approved: {},
      done: false,
      error: "",
    };
    if (h.objective === "simulate")
      for (const [i, fields] of Object.entries(h.scenario ?? {})) {
        const action = h.draft.actions[+i];
        const allowed = action?.type === "stock_order" ? "quantity" : "amount";
        if (!action || Object.keys(fields).some((f) => f !== allowed))
          return { ...state, error: "Invalid scenario handoff." };
        next.scenario[+i] = fields;
      }
    if (h.objective === "review" && Object.keys(effective(next).errors).length)
      return { ...state, error: "Check the draft before reviewing." };
    return next;
  }
  if (!state.handoff || state.done) return state;
  if (event.type === "objective") {
    if (event.value === "review" && Object.keys(effective(state).errors).length)
      return {
        ...state,
        error: "Check the highlighted fields before reviewing.",
      };
    return {
      ...state,
      handoff: { ...state.handoff, objective: event.value },
      scenario: {},
      approved: {},
      error: "",
    };
  }
  if (event.type === "edit" || event.type === "scenario") {
    if (state.handoff.objective === "review") return state;
    const action = state.handoff.draft.actions[event.index];
    if (!action) return state;
    if (event.type === "scenario") {
      if (
        state.handoff.objective !== "simulate" ||
        event.field !== (action.type === "stock_order" ? "quantity" : "amount")
      )
        return state;
      return {
        ...state,
        scenario: updateEdit(state.scenario, event.index, event.field, event.value),
        error: "",
      };
    }
    return {
      ...state,
      edits: updateEdit(state.edits, event.index, event.field, event.value),
      scenario: {},
      approved: {},
      error: "",
    };
  }
  if (event.type === "apply") {
    if (state.handoff.objective !== "simulate" || Object.keys(effective(state, true).errors).length)
      return state;
    const edits = structuredClone(state.edits);
    for (const [i, fields] of Object.entries(state.scenario)) edits[+i] = { ...edits[+i], ...fields };
    return {
      ...state,
      edits,
      scenario: {},
      approved: {},
      handoff: { ...state.handoff, objective: "prepare" },
    };
  }
  if (state.handoff.objective !== "review") return state;
  if (event.type === "approve" && state.handoff.draft.actions[event.index])
    return {
      ...state,
      approved: {
        ...state.approved,
        [event.index]: !state.approved[event.index],
      },
    };
  if (
    event.type === "finish" &&
    !Object.keys(effective(state).errors).length &&
    state.handoff.draft.actions.every((_, i) => state.approved[i])
  )
    return { ...state, done: true };
  return state;
}
