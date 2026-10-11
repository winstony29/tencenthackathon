import { z } from "zod";
import type { Accounts, Draft } from "./contracts";

export const layouts = ["focus", "split", "comparison", "timeline", "sequence", "canvas"] as const;
export type Layout = (typeof layouts)[number];
export type PresentationFacts = {
  mode: "transaction" | "decision";
  accounts: Accounts | null;
  scheduled: boolean;
};
const support = z.enum(["accounts", "holdings", "projection", "recipient"]);
export const manifestSchema = z.strictObject({
  version: z.literal(1),
  mode: z.enum(["transaction", "decision"]),
  layout: z.enum(layouts),
  primary: z.number().int().nonnegative(),
  tasks: z
    .array(
      z.strictObject({ action: z.number().int().nonnegative(), priority: z.number().int().min(1).max(4) }),
    )
    .min(1)
    .max(4),
  supporting: z.array(support).max(4),
  interaction: z.enum(["edit-review", "adjust-assumptions"]),
  source: z.enum(["jev", "demo", "fallback"]),
});
export type UIManifest = z.infer<typeof manifestSchema>;
export const selectionSchema = z.strictObject({
  mode: z.enum(["transaction", "decision"]),
  ranks: z
    .array(z.strictObject({ layout: z.enum(layouts), score: z.number().min(0).max(1) }))
    .min(1)
    .max(6),
  primary: z.number().int().nonnegative(),
  supporting: z.array(support).max(4),
});
export function eligibleLayouts(draft: Draft, facts: PresentationFacts): Layout[] {
  return [
    "split",
    ...(draft.actions.length === 1 ? ["focus" as const] : ["sequence" as const]),
    ...(facts.accounts && facts.accounts.accounts.length > 1 ? ["comparison" as const] : []),
    ...(facts.scheduled ? ["timeline" as const] : []),
    ...(facts.mode === "decision" ? ["canvas" as const] : []),
  ];
}
export function eligibleSupport(draft: Draft, facts: PresentationFacts): UIManifest["supporting"] {
  return [
    ...(facts.accounts ? ["accounts" as const] : []),
    ...(draft.actions.some((a) => a.type === "stock_order") && facts.accounts ? ["holdings" as const] : []),
    ...(facts.mode === "decision" && facts.accounts ? ["projection" as const] : []),
    ...(draft.actions.some((a) => a.type === "transfer") ? ["recipient" as const] : []),
  ];
}
export function validateManifest(value: unknown, draft: Draft, facts: PresentationFacts): UIManifest {
  const manifest = manifestSchema.parse(value);
  if (manifest.mode !== facts.mode || !eligibleLayouts(draft, facts).includes(manifest.layout))
    throw new Error("Ineligible workspace layout");
  const indexes = manifest.tasks.map((t) => t.action);
  if (
    indexes.length !== draft.actions.length ||
    new Set(indexes).size !== indexes.length ||
    indexes.some((i) => i >= draft.actions.length) ||
    !indexes.includes(manifest.primary)
  )
    throw new Error("Every draft action must be present exactly once");
  if (
    new Set(manifest.supporting).size !== manifest.supporting.length ||
    manifest.supporting.some((c) => !eligibleSupport(draft, facts).includes(c))
  )
    throw new Error("Ineligible supporting component");
  if (manifest.interaction !== (facts.mode === "decision" ? "adjust-assumptions" : "edit-review"))
    throw new Error("Invalid interaction pattern");
  return manifest;
}
// The server's future JEV result is a presentation choice only, never transaction data.
export function composeWorkspace(
  draft: Draft,
  facts: PresentationFacts,
  selection?: unknown,
  source: UIManifest["source"] = "fallback",
): UIManifest {
  const eligible = eligibleLayouts(draft, facts);
  const pick = selection === undefined ? null : selectionSchema.parse(selection);
  if (pick && pick.mode !== facts.mode) throw new Error("Presentation intent mismatch");
  const ranked = pick?.ranks.filter((r) => eligible.includes(r.layout)).sort((a, b) => b.score - a.score);
  if (pick && !ranked?.length) throw new Error("No eligible presentation choice");
  const layout =
    ranked?.[0]?.layout ??
    (facts.mode === "decision" ? "canvas" : draft.actions.length > 1 ? "sequence" : "focus");
  return validateManifest(
    {
      version: 1,
      mode: facts.mode,
      layout,
      primary: pick?.primary ?? 0,
      tasks: draft.actions.map((_, action) => ({
        action,
        priority:
          action === (pick?.primary ?? 0) ? 1 : action < (pick?.primary ?? 0) ? action + 2 : action + 1,
      })),
      supporting: pick?.supporting ?? eligibleSupport(draft, facts),
      interaction: facts.mode === "decision" ? "adjust-assumptions" : "edit-review",
      source,
    },
    draft,
    facts,
  );
}
