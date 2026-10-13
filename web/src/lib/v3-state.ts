import type { Draft } from "./contracts";
import { draftSchema } from "./contracts";
import { conversationReducer, initialConversation, type ConversationState } from "./v2-conversation";
import { editedActions, updateEdit, type DraftEdits } from "./draft-editor";
import { v2Accounts } from "./v2-parser";
import { exampleDraft } from "./examples";
import { objectiveSchema, type Objective } from "./v3-registry";

export type State = { task: ConversationState; objective: Objective; scenario: DraftEdits; error: string };
export const initialState: State = {
  task: initialConversation,
  objective: "prepare",
  scenario: {},
  error: "",
};
export type Handoff = { draft: Draft; objective: Objective; scenario?: DraftEdits };
export type Event =
  | { type: "handoff"; value: Handoff }
  | { type: "edit"; index: number; field: string; value: string }
  | { type: "scenario"; index: number; field: string; value: string }
  | { type: "apply" }
  | { type: "review" }
  | { type: "back" }
  | { type: "finish" }
  | { type: "reset" }
  | { type: "approve"; index: number };
export function effective(state: State) {
  if (!state.task.draft) return { actions: [], errors: {} };
  return editedActions(state.task.draft, state.task.edits, v2Accounts);
}
export function scenarioActions(state: State) {
  if (!state.task.draft) return { actions: [], errors: {} };
  const edits: DraftEdits = structuredClone(state.task.edits);
  for (const [index, values] of Object.entries(state.scenario))
    edits[Number(index)] = { ...edits[Number(index)], ...values };
  return editedActions(state.task.draft, edits, v2Accounts);
}
export function reducer(state: State, event: Event): State {
  if (event.type === "reset") return initialState;
  if (event.type === "handoff") {
    const { draft, objective } = event.value;
    const parsed = draftSchema.safeParse(draft);
    if (
      !parsed.success ||
      !objectiveSchema.safeParse(objective).success ||
      draft.actions.some(
        (a) =>
          a.type === "fx_conversion" || // V3 has no conversion layout; V5 shows them
          !v2Accounts.accounts.some((b) => b.account_id === a.from_account_id && b.currency === a.currency) ||
          (a.type === "transfer" && !v2Accounts.payees.some((p) => p.payee_id === a.to_payee_id)),
      )
    )
      return { ...state, error: "Waiting for a complete Winston task." };
    let task = conversationReducer(state.task, {
      type: "handoff",
      draft,
      mode: ["assess_affordability", "simulate"].includes(objective) ? "decision" : "transaction",
    });
    if (task.error) return { ...state, error: task.error };
    if (objective === "review") task = conversationReducer(task, { type: "review" });
    return {
      task,
      objective,
      scenario: objective === "simulate" ? (event.value.scenario ?? {}) : {},
      error: "",
    };
  }
  if (event.type === "scenario") {
    if (
      !state.task.draft ||
      state.task.surface !== "workspace" ||
      !["assess_affordability", "simulate"].includes(state.objective)
    )
      return state;
    const a = state.task.draft.actions[event.index];
    if (!a || event.field !== (a.type === "stock_order" ? "quantity" : "amount")) return state;
    return { ...state, scenario: updateEdit(state.scenario, event.index, event.field, event.value) };
  }
  if (event.type === "apply") {
    if (state.task.surface !== "workspace" || Object.keys(scenarioActions(state).errors).length) return state;
    let task = state.task;
    for (const [index, fields] of Object.entries(state.scenario))
      for (const [field, value] of Object.entries(fields))
        task = conversationReducer(task, { type: "edit", index: Number(index), field, value });
    return { ...state, task: { ...task, mode: "transaction" }, objective: "prepare", scenario: {} };
  }
  if (event.type === "edit") return { ...state, task: conversationReducer(state.task, event), scenario: {} };
  if (event.type === "review") {
    const task = conversationReducer(state.task, event);
    return task.surface === "review" ? { ...state, task, objective: "review", scenario: {} } : state;
  }
  if (event.type === "back")
    return {
      ...state,
      task: conversationReducer(state.task, { type: "return" }),
      objective: "prepare",
      scenario: {},
    };
  return { ...state, task: conversationReducer(state.task, event) };
}
export const journeys = {
  transfer: [
    { label: "Send", text: "Transfer $500 to John.", objective: "prepare" },
    { label: "Afford", text: "Can I afford that?", objective: "assess_affordability" },
    { label: "Explore", text: "What if I send $700 instead?", objective: "simulate" },
    { label: "Update", text: "Make it $300 from savings.", objective: "prepare" },
    { label: "Review", text: "Review the transfer.", objective: "review" },
  ],
  multi: [
    { label: "Plan", text: "Pay my credit card bill and buy 100 Apple shares.", objective: "prepare" },
    { label: "Afford", text: "Can I afford both?", objective: "assess_affordability" },
    { label: "Explore", text: "What if I buy 80 shares?", objective: "simulate" },
    { label: "Review", text: "Review both tasks.", objective: "review" },
  ],
} as const;
export function demoHandoff(journey: keyof typeof journeys, step: number, state: State): Handoff {
  const turn = journeys[journey][step];
  const initial = exampleDraft(journey === "transfer" ? 0 : 3);
  let draft =
    step > 0 && state.task.draft?.draft_id === initial.draft_id
      ? structuredClone(state.task.draft)
      : structuredClone(initial);
  if (journey === "transfer" && step === 3)
    draft = { ...draft, actions: [{ ...initial.actions[0], amount: "300.00" } as Draft["actions"][number]] };
  return {
    draft,
    objective: turn.objective,
    scenario:
      turn.objective === "simulate"
        ? journey === "transfer"
          ? { 0: { amount: "700.00" } }
          : { 1: { quantity: "80" } }
        : undefined,
  };
}
