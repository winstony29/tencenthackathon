import { z } from "zod";
import catalog from "../../presentation-catalog.json";
import type { Accounts, Draft, Quote } from "./contracts";
import { fromCurrency } from "./money";

export const objectiveSchema = z.enum([
  "prepare",
  "assess_affordability",
  "simulate",
  "check_recipient",
  "review",
]);
export type Objective = z.infer<typeof objectiveSchema>;
export type ComponentId = keyof typeof catalog;
export const componentIds = Object.keys(catalog) as ComponentId[];
export const registry = catalog;
export const bindingSchema = z.strictObject({
  component: z.enum(componentIds as [ComponentId, ...ComponentId[]]),
  action: z.number().int().min(0).max(3).nullable(),
});
export type Binding = z.infer<typeof bindingSchema>;
export type Candidate = Binding & {
  key: string;
  required: boolean;
  primary: boolean;
};
export const keyOf = (b: Binding) => `${b.component}${b.action === null ? "" : `:${b.action}`}`;
export const contextSchema = z.strictObject({
  action_types: z
    .array(z.enum(["transfer", "bill_payment", "stock_order"]))
    .min(1)
    .max(4),
  complete: z.literal(true),
  missing: z.array(z.string()).max(0),
  objective: objectiveSchema,
  recipient_status: z.enum(["listed", "not_applicable"]),
  available_context: z.array(
    z.enum([
      "accounts",
      "recipients",
      "billers",
      "quote",
      "holdings",
      "projection",
      "multiple",
      "alternatives",
    ]),
  ),
  viewport: z.enum(["mobile", "desktop"]).default("desktop"),
  current_ui: z
    .strictObject({
      primary: z.string(),
      components: z.array(z.string()).max(20),
      layout: z.enum(["focus", "split", "sequence"]),
      density: z.enum(["compact", "comfortable"]),
    })
    .nullable(),
});
export type Context = z.infer<typeof contextSchema>;
export function makeContext(
  draft: Draft,
  objective: Objective,
  accounts: Accounts,
  quotes: Record<string, Quote>,
): Context {
  // V3 state refuses drafts with a conversion, so none reaches here.
  const types = draft.actions.flatMap((a) => (a.type === "fx_conversion" ? [] : [a.type]));
  const quoted = draft.actions
    .filter((a) => a.type === "stock_order")
    .every((a) => quotes[a.ticker]?.ticker === a.ticker && quotes[a.ticker]?.currency === a.currency);
  const data: Context["available_context"] = ["accounts", "projection"];
  if (accounts.payees.length) data.push("recipients");
  if (accounts.billers.length) data.push("billers");
  if (quoted && types.includes("stock_order")) data.push("quote");
  if (accounts.holdings.length) data.push("holdings");
  if (types.length > 1) data.push("multiple");
  if (draft.actions.some((a) => accounts.accounts.filter((b) => b.currency === fromCurrency(a)).length > 1))
    data.push("alternatives");
  return {
    action_types: types,
    complete: true,
    missing: [],
    objective,
    recipient_status: types.includes("transfer") ? "listed" : "not_applicable",
    available_context: data,
    viewport: "desktop",
    current_ui: null,
  };
}
export function candidates(context: Context): Candidate[] {
  return componentIds.flatMap((component) => {
    const entry = registry[component];
    if (
      !(entry.objectives as string[]).includes(context.objective) ||
      !entry.data.every((d) => (context.available_context as string[]).includes(d))
    )
      return [];
    const actions = context.action_types.flatMap((type, index) =>
      (entry.types as string[]).includes(type) ? [index] : [],
    );
    if (!actions.length) return [];
    return (entry.perAction ? actions : [null]).map((action) => ({
      component,
      action,
      key: keyOf({ component, action }),
      required: (entry.requiredFor as string[]).includes(context.objective),
      primary: (entry.primaryFor as string[]).includes(context.objective),
    }));
  });
}
export function offeredQuestions(context: Context) {
  const items = candidates(context);
  const questions: Record<string, string[]> = {
    primary: items.filter((c) => c.primary).map((c) => c.key),
    layout: context.action_types.length > 1 ? ["split", "sequence"] : ["focus", "split"],
    density: ["compact", "comfortable"],
    interaction:
      context.objective === "review"
        ? ["review"]
        : context.objective === "simulate"
          ? ["adjust"]
          : context.objective === "prepare"
            ? ["edit", "inspect"]
            : ["inspect"],
    continuity: context.current_ui ? ["preserve", "recompose"] : ["recompose"],
  };
  items
    .filter((c) => !c.required)
    .forEach((c) => {
      questions[`show_${c.key}`] = ["include", "omit"];
    });
  return questions;
}
