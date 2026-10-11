import { z } from "zod";
import { composeWorkspace, eligibleLayouts, eligibleSupport, layouts, type UIManifest } from "./composition";
import type { ConversationState } from "./v2-conversation";
import { v2Accounts } from "./v2-parser";

export const presentationChoiceSchema = z.strictObject({
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
      ? [state.partial?.recipient ? "amount" : "recipient"]
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
