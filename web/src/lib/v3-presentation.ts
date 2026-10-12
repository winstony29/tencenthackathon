import { z } from "zod";
import {
  bindingSchema,
  candidates,
  keyOf,
  offeredQuestions,
  registry,
  type Context,
  type Objective,
} from "./v3-registry";

const answerSchema = z.strictObject({
  type: z.literal("choice"),
  choice: z.string(),
  confidence: z.number().min(0).max(1),
  probabilities: z.record(z.string(), z.number().min(0).max(1)),
});
export const replySchema = z.strictObject({
  source: z.literal("jev"),
  model: z.string().startsWith("jev-"),
  answers: z.record(z.string(), answerSchema),
  latency_ms: z.number().nonnegative(),
});
export type Reply = z.infer<typeof replySchema>;
export const nodeSchema = bindingSchema.extend({
  key: z.string(),
  size: z.enum(["hero", "wide", "compact"]),
  slot: z.enum(["primary", "secondary", "support"]),
});
export const manifestSchema = z.strictObject({
  version: z.literal(3),
  objective: z.enum(["prepare", "assess_affordability", "simulate", "check_recipient", "review"]),
  layout: z.enum(["focus", "split", "sequence"]),
  density: z.enum(["compact", "comfortable"]),
  interaction: z.enum(["edit", "inspect", "adjust", "review"]),
  nodes: z.array(nodeSchema).min(1).max(20),
  source: z.enum(["jev", "fallback"]),
});
export type Manifest = z.infer<typeof manifestSchema>;
export type Composition = {
  manifest: Manifest;
  rejected: string | null;
  stability: string | null;
  changes: string[];
};
function signature(context: Context) {
  return JSON.stringify([context.objective, context.action_types, context.available_context]);
}
export function validateManifest(value: unknown, context: Context): Manifest {
  const m = manifestSchema.parse(value),
    items = candidates(context),
    questions = offeredQuestions(context);
  if (
    m.objective !== context.objective ||
    !questions.layout.includes(m.layout) ||
    !questions.interaction.includes(m.interaction)
  )
    throw Error("Incompatible presentation.");
  const keys = m.nodes.map((n) => n.key);
  if (new Set(keys).size !== keys.length || m.nodes.filter((n) => n.slot === "primary").length !== 1)
    throw Error("Invalid component slots.");
  for (const node of m.nodes) {
    const item = items.find((c) => c.key === node.key),
      def = registry[node.component];
    if (
      !item ||
      keyOf(node) !== node.key ||
      !(def.sizes as string[]).includes(node.size) ||
      !(def.slots as string[]).includes(node.slot) ||
      !(def.interactions as string[]).includes(m.interaction)
    )
      throw Error("Ineligible component binding.");
    if (node.slot === "primary" && (!item.primary || node.size !== "hero"))
      throw Error("Invalid primary component.");
  }
  if (items.some((c) => c.required && !keys.includes(c.key))) throw Error("Missing required component.");
  if (keys.includes("amount_simulator") && !keys.includes("balance_projection"))
    throw Error("Simulation needs a projection.");
  return m;
}
export function compose(
  context: Context,
  input?: unknown,
  previous?: { context: Context; manifest: Manifest },
): Composition {
  const items = candidates(context),
    questions = offeredQuestions(context);
  if (!questions.primary.length) throw Error("No complete supported task to present.");
  let picks: Record<string, string> = {
    primary: questions.primary[0],
    layout: questions.layout[0],
    density: "comfortable",
    interaction: questions.interaction[0],
    continuity: "recompose",
  };
  let rejected: string | null = input ? null : "Live composition unavailable.",
    stability: string | null = null;
  let reply: Reply | undefined;
  try {
    if (input) {
      reply = replySchema.parse(input);
      if (Object.keys(reply.answers).length !== Object.keys(questions).length)
        throw Error("Unexpected answer count.");
      for (const [name, options] of Object.entries(questions)) {
        const a = reply.answers[name];
        if (
          !a ||
          !options.includes(a.choice) ||
          Object.keys(a.probabilities).sort().join() !== [...options].sort().join() ||
          Math.abs(Object.values(a.probabilities).reduce((s, p) => s + p, 0) - 1) > 0.05
        )
          throw Error("Invalid offered choice or probabilities.");
      }
      picks = Object.fromEntries(Object.entries(reply.answers).map(([k, a]) => [k, a.choice]));
    }
  } catch {
    rejected = "Invalid JEV response; using local composition.";
    reply = undefined;
  }
  let keys = items
    .filter((c) => c.required || c.key === picks.primary || picks[`show_${c.key}`] === "include")
    .map((c) => c.key);
  if (keys.includes("amount_simulator") && !keys.includes("balance_projection"))
    keys.push("balance_projection");
  const same = previous && signature(previous.context) === signature(context);
  if (reply && same) {
    try {
      const prior = validateManifest(previous.manifest, context);
      const probabilities = Object.values(reply.answers.primary.probabilities).sort((a, b) => b - a);
      if (
        picks.continuity === "preserve" ||
        (probabilities.length > 1 && probabilities[0] - probabilities[1] < 0.15)
      ) {
        keys = prior.nodes.map((n) => n.key);
        picks.primary = prior.nodes.find((n) => n.slot === "primary")!.key;
        picks.layout = prior.layout;
        picks.density = prior.density;
        stability = "Kept eligible composition for the same objective.";
      }
    } catch {
      /* An obsolete composition is never preserved. */
    }
  }
  const manifest = validateManifest(
    {
      version: 3,
      objective: context.objective,
      layout: picks.layout,
      density: picks.density,
      interaction: picks.interaction,
      source: reply ? "jev" : "fallback",
      nodes: keys.map((key) => {
        const c = items.find((c) => c.key === key)!;
        const slot =
          key === picks.primary
            ? "primary"
            : (registry[c.component].slots as string[]).includes("secondary") &&
                (c.required || c.component === "amount_simulator")
              ? "secondary"
              : "support";
        return {
          component: c.component,
          action: c.action,
          key,
          slot,
          size: slot === "primary" ? "hero" : slot === "secondary" ? "wide" : "compact",
        };
      }),
    },
    context,
  );
  const old = previous?.manifest.nodes.map((n) => n.key) ?? [];
  return {
    manifest,
    rejected,
    stability,
    changes: [
      ...keys.filter((k) => !old.includes(k)).map((k) => `Added ${k}`),
      ...old.filter((k) => !keys.includes(k)).map((k) => `Removed ${k}`),
      `Primary: ${picks.primary}`,
    ],
  };
}
export const objectiveLabels: Record<Objective, string> = {
  prepare: "Make your move.",
  assess_affordability: "Room for your next move.",
  simulate: "A little more. Or less.",
  check_recipient: "The right person.",
  review: "Ready when you are.",
};
