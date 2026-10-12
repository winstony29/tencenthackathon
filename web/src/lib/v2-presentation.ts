import { z } from "zod";
import { composeWorkspace, eligibleLayouts, eligibleSupport, layouts, type UIManifest } from "./composition";
import type { ConversationState } from "./v2-conversation";
import { v2Accounts } from "./v2-parser";

export const presentationChoiceSchema = z.strictObject({
  bundle: z
    .enum([
      "transfer_editing",
      "recipient_check",
      "affordability_overview",
      "affordability_simulation",
      "investment_assessment",
      "multi_task_review",
    ])
    .optional(),
  layout: z.enum(layouts),
  interaction: z.enum(["execute", "clarify", "compare", "simulate", "explore"]),
  focus: z.enum(["amount", "recipient", "affordability", "deadline", "risk"]),
  supporting: z.array(z.enum(["accounts", "holdings", "projection", "recipient"])).max(4),
  tools: z.array(z.enum(["form", "slider", "comparison", "chart", "timeline"])).max(5),
  density: z.enum(["minimal", "standard", "detailed"]),
  strategy: z.enum(["partial", "focused", "multi"]),
  primary: z.number().int().nonnegative(),
});
export type PresentationChoice = z.infer<typeof presentationChoiceSchema>;
export const livePresentationSchema = z.strictObject({
  source: z.literal("jev"),
  selection: presentationChoiceSchema,
  systemone: z.strictObject({
    model: z.string().min(1),
    answers: z.record(
      z.string(),
      z.strictObject({
        type: z.literal("choice"),
        choice: z.string(),
        confidence: z.number().min(0).max(1),
        probabilities: z.record(z.string(), z.number().min(0).max(1)),
      }),
    ),
  }),
});
export type V2Manifest = PresentationChoice & {
  version: 2;
  workspace: UIManifest | null;
  source: "fallback" | "jev";
};
export function presentationOptions(state: ConversationState) {
  const facts = { mode: state.mode, accounts: v2Accounts, scheduled: false };
  const partial = !state.draft;
  const recipientFocus = state.draft?.actions.some((a) => a.type === "transfer") ? ["recipient"] : [];
  return {
    layout: state.draft ? eligibleLayouts(state.draft, facts) : ["focus"],
    interaction: partial
      ? ["clarify"]
      : state.mode === "decision"
        ? ["simulate", "compare", "explore"]
        : ["execute", "explore", "compare"],
    focus: partial
      ? ["recipient"]
      : state.mode === "decision"
        ? ["affordability", "amount", ...recipientFocus, "risk"]
        : [
            "amount",
            ...recipientFocus,
            ...(state.draft?.actions.some((a) => a.type === "stock_order") ? ["risk"] : []),
          ],
    supporting: state.draft ? eligibleSupport(state.draft, facts) : ["accounts", "recipient"],
    tools: partial
      ? ["form"]
      : state.mode === "decision"
        ? ["form", "slider", "comparison", "chart"]
        : ["form", "comparison"],
    density: ["minimal", "standard", "detailed"],
    strategy: [partial ? "partial" : state.draft!.actions.length > 1 ? "multi" : "focused"],
  };
}
export function present(
  state: ConversationState,
  value?: unknown,
): {
  manifest: V2Manifest;
  trace: {
    eligible: ReturnType<typeof presentationOptions>;
    selection: PresentationChoice;
    fallbackReason: string | null;
  };
} {
  const options = presentationOptions(state);
  const fallback: PresentationChoice = {
    layout: !state.draft
      ? "focus"
      : state.mode === "decision"
        ? "canvas"
        : state.draft.actions.length > 1
          ? "sequence"
          : "focus",
    interaction: !state.draft ? "clarify" : state.mode === "decision" ? "simulate" : "execute",
    focus: options.focus[0] as PresentationChoice["focus"],
    supporting: options.supporting as PresentationChoice["supporting"],
    tools: !state.draft ? ["form"] : state.mode === "decision" ? ["form", "slider", "chart"] : ["form"],
    density: !state.draft ? "minimal" : state.mode === "decision" ? "detailed" : "standard",
    strategy: options.strategy[0] as PresentationChoice["strategy"],
    primary: 0,
  };
  let choice = fallback;
  let fallbackReason: string | null = "No live selection supplied; deterministic presentation fallback.";
  if (value !== undefined) {
    const parsed = presentationChoiceSchema.safeParse(value);
    if (parsed.success) {
      const c = parsed.data;
      const scalar = ["layout", "interaction", "focus", "density", "strategy"] as const;
      const valid =
        scalar.every((key) => options[key].includes(c[key])) &&
        c.primary < (state.draft?.actions.length ?? 1) &&
        c.supporting.every((s) => options.supporting.includes(s)) &&
        new Set(c.supporting).size === c.supporting.length &&
        c.tools.includes("form") &&
        c.tools.every((t) => options.tools.includes(t)) &&
        new Set(c.tools).size === c.tools.length;
      if (valid) {
        choice = c;
        fallbackReason = null;
      } else fallbackReason = "JEV selected an ineligible option; safe fallback used.";
    } else fallbackReason = "JEV response failed the strict presentation schema; safe fallback used.";
  }
  const source = fallbackReason ? "fallback" : "jev";
  const workspace = state.draft
    ? composeWorkspace(
        state.draft,
        { mode: state.mode, accounts: v2Accounts, scheduled: false },
        {
          mode: state.mode,
          ranks: [{ layout: choice.layout, score: 1 }],
          primary: choice.primary,
          supporting: choice.supporting,
        },
        source,
      )
    : null;
  return {
    manifest: { ...choice, version: 2, workspace, source },
    trace: { eligible: options, selection: choice, fallbackReason },
  };
}
// Generic selection transport boundary; the live placeholder uses the isolated /api/presentation-demo router.
// Only approved option IDs and structural context cross this boundary; never free-form financial values.
export type JevPresentationTransport = (request: {
  version: 2;
  options: ReturnType<typeof presentationOptions>;
  actionTypes: string[];
}) => Promise<unknown>;
export async function decidePresentation(state: ConversationState, transport?: JevPresentationTransport) {
  if (!transport) return present(state);
  try {
    return present(
      state,
      await transport({
        version: 2,
        options: presentationOptions(state),
        actionTypes: state.draft?.actions.map((a) => a.type) ?? ["transfer"],
      }),
    );
  } catch {
    const result = present(state);
    result.trace.fallbackReason = "JEV transport unavailable; safe fallback used.";
    return result;
  }
}

export type Objective = "prepare" | "assess_affordability" | "check_recipient";
export type Bundle = NonNullable<PresentationChoice["bundle"]>;
export function eligibleBundles(state: ConversationState, objective: Objective): Bundle[] {
  if (!state.draft) return [];
  if (objective === "assess_affordability") return ["affordability_overview", "affordability_simulation"];
  if (objective === "check_recipient")
    return state.draft.actions.some((a) => a.type === "transfer") ? ["recipient_check"] : [];
  if (state.draft.actions.length > 1)
    return [
      "multi_task_review",
      ...(state.draft.actions.some((a) => a.type === "stock_order")
        ? ["investment_assessment" as const]
        : []),
    ];
  return state.draft.actions[0].type === "transfer"
    ? ["transfer_editing", "recipient_check"]
    : state.draft.actions[0].type === "stock_order"
      ? ["investment_assessment"]
      : ["multi_task_review"];
}
export function bundleSelection(
  state: ConversationState,
  bundle: Bundle,
  layout: PresentationChoice["layout"],
  density: PresentationChoice["density"],
  primary = 0,
): PresentationChoice {
  const support = presentationOptions(state).supporting;
  const affordability = bundle.startsWith("affordability_");
  return {
    bundle,
    layout,
    density,
    primary,
    interaction: affordability ? (bundle === "affordability_simulation" ? "simulate" : "explore") : "execute",
    focus: affordability
      ? "affordability"
      : bundle === "recipient_check"
        ? "recipient"
        : bundle === "investment_assessment"
          ? "risk"
          : "amount",
    strategy: state.draft!.actions.length > 1 ? "multi" : "focused",
    supporting: support.filter((s) =>
      affordability
        ? s === "accounts" || s === "projection"
        : bundle === "recipient_check"
          ? s === "recipient"
          : bundle === "investment_assessment"
            ? s === "accounts" || s === "holdings"
            : s === "accounts",
    ) as PresentationChoice["supporting"],
    tools:
      bundle === "affordability_simulation"
        ? ["form", "slider", "chart"]
        : affordability
          ? ["form", "chart"]
          : ["form"],
  };
}
export function composeBundles(
  state: ConversationState,
  objective: Objective,
  reply?: z.infer<typeof livePresentationSchema>,
  previous?: { objective: Objective; selection: PresentationChoice },
) {
  const eligible = eligibleBundles(state, objective);
  if (!eligible.length) throw new Error("Await a complete Winston output before composing UI.");
  const fallbackLayout =
    state.mode === "decision" ? "canvas" : state.draft!.actions.length > 1 ? "sequence" : "focus";
  const fallback = bundleSelection(state, eligible[0], fallbackLayout, "standard");
  let selection = fallback;
  let stabilityReason: string | null = null;
  let failure: string | null = null;
  if (reply) {
    const c = reply.selection;
    if (!c.bundle || !eligible.includes(c.bundle)) failure = "Ineligible presentation bundle.";
    else {
      const expected = bundleSelection(state, c.bundle, c.layout, c.density, c.primary);
      if (
        JSON.stringify(expected.supporting) !== JSON.stringify(c.supporting) ||
        JSON.stringify(expected.tools) !== JSON.stringify(c.tools) ||
        expected.focus !== c.focus ||
        expected.interaction !== c.interaction ||
        expected.strategy !== c.strategy ||
        present(state, expected).manifest.source !== "jev"
      )
        failure = "Invalid bundle combination.";
      else selection = expected;
    }
    if (
      !failure &&
      previous?.objective === objective &&
      previous.selection.bundle &&
      eligible.includes(previous.selection.bundle) &&
      present(state, previous.selection).manifest.source === "jev"
    ) {
      const weak = (name: string) => {
        const probabilities = Object.values(reply.systemone.answers[name]?.probabilities ?? {}).sort(
          (a, b) => b - a,
        );
        return probabilities.length > 1 && probabilities[0] - probabilities[1] < 0.15;
      };
      if (weak("bundle")) {
        selection = { ...previous.selection };
        stabilityReason = "Retained eligible bundle: unchanged objective and weak preference.";
      } else if (weak("layout")) {
        selection = { ...selection, layout: previous.selection.layout };
        stabilityReason = "Retained eligible layout: unchanged objective and weak preference.";
      }
    }
  }
  const result = present(state, selection);
  if (!reply || failure) {
    result.manifest.source = "fallback";
    if (result.manifest.workspace) result.manifest.workspace.source = "fallback";
    result.trace.fallbackReason = failure ?? "No live bundle selection; deterministic presentation fallback.";
  }
  return { ...result, bundleTrace: { objective, eligible, stabilityReason, marginThreshold: 0.15 } };
}
export function handoffContext(
  state: ConversationState,
  objective: Objective,
  recipientStatus: "listed" | "not_applicable",
  current?: PresentationChoice,
) {
  if (!state.draft) throw new Error("Winston must resolve missing information first.");
  return {
    action_types: state.draft.actions.map((a) => a.type),
    complete: true,
    missing: [],
    objective,
    recipient_status: recipientStatus,
    available_context: [
      "account_balance",
      ...(state.mode === "decision" ? ["projected_balance"] : []),
      ...(state.draft.actions.some((a) => a.type === "stock_order") ? ["stock_quote", "holdings"] : []),
    ],
    current_ui: current?.bundle
      ? { bundle: current.bundle, layout: current.layout, density: current.density }
      : null,
  };
}
