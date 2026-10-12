import { z } from "zod";
import { editedActions, projectedBalances } from "./draft-editor";
import type { State } from "./v4-state";

const money = z.string().regex(/^\d+\.\d{2}$/);
export const financialSchema = z
  .strictObject({
    as_of: z.iso.date(),
    accounts: z
      .array(
        z.strictObject({
          account_id: z.string().min(1),
          // Omitted means unknown; an empty list means no known bills in this window.
          commitments: z
            .strictObject({
              through: z.iso.date(),
              bills: z
                .array(
                  z.strictObject({
                    id: z.string().min(1),
                    label: z.string().min(1).max(80),
                    due: z.iso.date(),
                    amount: money,
                    covered_by_draft_action: z.number().int().min(0).max(3).optional(),
                  }),
                )
                .max(30),
            })
            .optional(),
          reserve: money.optional(),
          spending: z
            .strictObject({
              from: z.iso.date(),
              through: z.iso.date(),
              categories: z
                .array(
                  z.strictObject({
                    label: z.string().min(1).max(40),
                    amount: money,
                  }),
                )
                .max(12),
            })
            .optional(),
        }),
      )
      .max(20),
  })
  .superRefine((v, ctx) => {
    if (new Set(v.accounts.map((a) => a.account_id)).size !== v.accounts.length)
      ctx.addIssue({ code: "custom", message: "Duplicate financial account" });
    for (const a of v.accounts) {
      const c = a.commitments;
      if (
        c &&
        (c.through < v.as_of ||
          new Set(c.bills.map((b) => b.id)).size !== c.bills.length ||
          c.bills.some((b) => b.due < v.as_of || b.due > c.through))
      )
        ctx.addIssue({ code: "custom", message: "Invalid commitments window" });
      if (a.spending && (a.spending.from > a.spending.through || a.spending.through > v.as_of))
        ctx.addIssue({ code: "custom", message: "Invalid spending period" });
    }
  });
export const cents = (v: string) => BigInt(v.replace(".", ""));
export const decimal = (v: bigint) =>
  `${v < 0n ? "-" : ""}${(v < 0n ? -v : v) / 100n}.${String((v < 0n ? -v : v) % 100n).padStart(2, "0")}`;
export function financialRows(state: State) {
  if (!state.handoff) return [];
  const edits = structuredClone(state.edits);
  for (const [i, fields] of Object.entries(state.scenario)) edits[+i] = { ...edits[+i], ...fields };
  const { actions, errors } = editedActions(state.handoff.draft, edits, state.handoff.data.accounts);
  if (Object.keys(errors).length) return [];
  const { data } = state.handoff;
  return projectedBalances(actions, data.accounts, data.quotes).map((a) => {
    const context = data.financial?.accounts.find((c) => c.account_id === a.account_id);
    const bills = context?.commitments?.bills.flatMap((b) => {
      const task = b.covered_by_draft_action === undefined ? undefined : actions[b.covered_by_draft_action];
      if (task?.type !== "bill_payment") return [b];
      // The linked obligation is paid even if the user changes its funding account.
      const outstanding = cents(b.amount) - cents(task.amount);
      return outstanding > 0n ? [{ ...b, amount: decimal(outstanding) }] : [];
    });
    const committed = bills?.reduce((sum, b) => sum + cents(b.amount), 0n);
    const remaining = a.projected === null ? null : cents(a.projected);
    const afterBills = remaining === null || committed === undefined ? null : remaining - committed;
    const reserve = context?.reserve === undefined ? null : cents(context.reserve);
    return { ...a, context, bills, committed, remaining, afterBills, reserve };
  });
}
