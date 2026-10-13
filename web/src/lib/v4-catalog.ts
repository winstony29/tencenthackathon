import { financialRows } from "./v4-financial";
import { z } from "zod";
import definitions from "../../v4-catalog.json";
import type {
  Experimental_CompositionCandidate,
  Experimental_CompositionCatalog,
} from "../../vendor/json-render/experimental-compose";
import type { State } from "./v4-state";
import { objectiveSchema } from "./v4-state";
import { fromCurrency } from "./money";

export const contextSchema = z.strictObject({
  objective: objectiveSchema,
  action_types: z
    .array(z.enum(["transfer", "bill_payment", "stock_order", "account_transfer", "fx_conversion"]))
    .min(1)
    .max(4),
  alternatives: z.boolean(),
  financial_context: z.boolean().optional(),
  funding: z.enum(["sufficient", "shortfall", "unknown"]).optional(),
  commitments: z.enum(["present", "none", "unknown"]).optional(),
  reserve: z.enum(["retained", "breached", "unknown"]).optional(),
  viewport: z.enum(["mobile", "desktop"]),
});
export type Context = z.infer<typeof contextSchema>;
export type Component = keyof typeof definitions.components;
type Definition = {
  description: string;
  objectives: string[];
  variants: Record<string, string>;
  slots?: string[];
  perAction?: boolean;
  types?: string[];
  alternatives?: boolean;
  binding: string;
  financial?: boolean;
};
export const components = definitions.components as Record<Component, Definition>;
export const prompts = definitions.prompts;
export type Recipe = Experimental_CompositionCandidate & {
  component: Component;
  action: number | null;
  variant: string;
};
export function contextFor(state: State, viewport: Context["viewport"]): Context {
  if (!state.handoff) throw Error("No resolved task.");
  const h = state.handoff;
  const rows = financialRows(state);
  return {
    objective: h.objective,
    financial_context: rows.some((a) => !!a.context),
    funding: rows.some(
      (a) => (a.remaining !== null && a.remaining < 0n) || (a.afterBills !== null && a.afterBills < 0n),
    )
      ? "shortfall"
      : !rows.length || rows.some((a) => a.remaining === null)
        ? "unknown"
        : "sufficient",
    commitments: rows.some((a) => a.bills?.length)
      ? "present"
      : !rows.length || rows.some((a) => !a.bills)
        ? "unknown"
        : "none",
    reserve: rows.some((a) => a.reserve !== null && a.afterBills !== null && a.afterBills < a.reserve)
      ? "breached"
      : !rows.length || rows.some((a) => a.reserve === null || a.afterBills === null)
        ? "unknown"
        : "retained",
    action_types: h.draft.actions.map((a) => a.type),
    viewport,
    alternatives: h.draft.actions.some(
      (a) => h.data.accounts.accounts.filter((b) => b.currency === fromCurrency(a)).length > 1,
    ),
  };
}
export function recipes(context: Context): Recipe[] {
  return (Object.entries(components) as [Component, Definition][]).flatMap(([component, d]) => {
    if (d.financial && !context.financial_context) return [];
    if (!d.objectives.includes(context.objective) || (d.alternatives && !context.alternatives)) return [];
    const actions = d.perAction
      ? context.action_types.flatMap((t, i) => (!d.types || d.types.includes(t) ? [i] : []))
      : [null];
    return actions.flatMap((action) =>
      Object.entries(d.variants).flatMap(([variant, description]) => {
        if (variant === "funding-gap" && context.funding !== "shortfall") return [];
        if (component === "Workspace" && context.viewport === "mobile" && variant === "split") return [];
        return [
          {
            id: `${component}_${action ?? "all"}_${variant}`,
            component,
            action,
            variant,
            description: `${component}${action === null ? "" : ` for task ${action + 1} (${context.action_types[action]})`}: ${d.description} ${description}`,
            root: component === "Workspace",
            resource: `${component}_${action ?? "all"}`,
            element: {
              type: component,
              props: { action, variant, binding: d.binding },
            },
          },
        ];
      }),
    );
  });
}
export const specSchema = z.strictObject({
  root: z.string().min(1),
  elements: z.record(
    z.string(),
    z.strictObject({
      type: z.string(),
      props: z.record(z.string(), z.unknown()),
      children: z.array(z.string()).optional(),
      slots: z.record(z.string(), z.array(z.string())).optional(),
    }),
  ),
  state: z.strictObject({}).optional(),
});
export const catalog: Experimental_CompositionCatalog = {
  data: {
    components: Object.fromEntries(
      Object.entries(components).map(([name, d]) => [
        name,
        {
          props: z.strictObject({
            action: d.perAction ? z.number().int().min(0).max(3) : z.null(),
            variant: z.enum(Object.keys(d.variants) as [string, ...string[]]),
            binding: z.literal(d.binding),
          }),
          slots: d.slots,
        },
      ]),
    ),
  },
  validate: (spec) => ({ success: specSchema.safeParse(spec).success }),
};
