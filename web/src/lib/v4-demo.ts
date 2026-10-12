import { exampleDraft, exampleQuote } from "./examples";
import { v2Accounts } from "./v2-parser";
import { effective, type Handoff, type State } from "./v4-state";

export const journeys = {
  transfer: [
    { label: "Transfer", text: "Transfer $500 to John.", objective: "prepare" },
    {
      label: "Affordability",
      text: "Can I afford that?",
      objective: "assess_affordability",
    },
    {
      label: "What if",
      text: "What if I send $700 instead?",
      objective: "simulate",
    },
    {
      label: "Update",
      text: "Actually make it $300 from savings.",
      objective: "prepare",
    },
    { label: "Review", text: "Review the transfer.", objective: "review" },
  ],
  multi: [
    {
      label: "Pay & invest",
      text: "Pay my credit card bill and buy 100 Apple shares.",
      objective: "prepare",
    },
    {
      label: "Affordability",
      text: "Can I afford both?",
      objective: "assess_affordability",
    },
    {
      label: "What if",
      text: "What if I buy 80 shares?",
      objective: "simulate",
    },
    { label: "Review", text: "Review both tasks.", objective: "review" },
  ],
} as const;
export function demoHandoff(journey: keyof typeof journeys, step: number, state: State): Handoff {
  const original = exampleDraft(journey === "transfer" ? 0 : 3);
  const same = step > 0 && state.handoff?.draft.draft_id === original.draft_id;
  const draft = structuredClone(same ? state.handoff!.draft : original);
  if (same && !Object.keys(effective(state).errors).length) draft.actions = effective(state).actions;
  if (journey === "transfer" && step === 3 && draft.actions[0].type === "transfer")
    draft.actions[0].amount = "300.00";
  const turn = journeys[journey][step];
  return {
    draft,
    objective: turn.objective,
    data: same
      ? state.handoff!.data
      : {
          accounts: structuredClone(v2Accounts),
          quotes: { AAPL: exampleQuote },
          source: "mock",
          financial: {
            as_of: "2026-10-12",
            accounts: [
              {
                account_id: "acc_1234",
                reserve: "1500.00",
                commitments: {
                  through: "2026-10-31",
                  bills: [
                    {
                      id: "rent",
                      label: "Rent",
                      due: "2026-10-20",
                      amount: "1100.00",
                    },
                    {
                      id: "utilities",
                      label: "Utilities",
                      due: "2026-10-24",
                      amount: "160.00",
                    },
                  ],
                },
                spending: {
                  from: "2026-09-01",
                  through: "2026-09-30",
                  categories: [
                    { label: "Housing", amount: "1100.00" },
                    { label: "Food", amount: "420.00" },
                    { label: "Transport", amount: "180.00" },
                    { label: "Other", amount: "240.00" },
                  ],
                },
              },
            ],
          },
        },
    scenario:
      turn.objective === "simulate"
        ? journey === "transfer"
          ? { 0: { amount: "700.00" } }
          : { 1: { quantity: "80" } }
        : undefined,
  };
}
