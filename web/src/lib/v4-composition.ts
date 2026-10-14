import { z } from "zod";
import { experimental_composeSpec } from "../../vendor/json-render/experimental-compose";
import { indexTree } from "../../vendor/json-render/experimental-composition-tree";
import type { Spec } from "../../vendor/json-render/types";
import { catalog, prompts, recipes, specSchema, type Context, type Recipe } from "./v4-catalog";
import { post } from "./api";

export type Node = {
  id: string;
  recipe: Recipe;
  parent: string | null;
  slot: string;
};
export type Composition = {
  spec: Spec;
  nodes: Node[];
  layout: string;
  source: "jev" | "fallback";
  reason: string | null;
  calls: Evaluation[];
  elapsedMs: number;
};
const answer = z.strictObject({
  choice: z.string(),
  confidence: z.number().min(0).max(1),
});
export const evaluationSchema = z.strictObject({
  answers: z.record(z.string(), answer),
  model: z.string(),
  latency_ms: z.number().nonnegative(),
  usage: z.strictObject({
    input_tokens: z.number().int().nonnegative(),
    output_tokens: z.number().int().nonnegative(),
  }),
});
export type Evaluation = z.infer<typeof evaluationSchema>;
export function accept(value: unknown, context: Context): Node[] {
  const spec = specSchema.parse(value) as Spec;
  if (!Object.keys(spec.elements).length || Object.keys(spec.elements).length > 24)
    throw Error("Invalid workspace size.");
  const positions = indexTree(spec, catalog, 3),
    offered = recipes(context),
    seen = new Set<string>();
  const nodes: Node[] = [];
  for (const [id, e] of Object.entries(spec.elements)) {
    const r = offered.find(
      (r) => r.component === e.type && r.variant === e.props.variant && r.action === e.props.action,
    );
    if (
      !r ||
      JSON.stringify(Object.entries(e.props).sort()) !==
        JSON.stringify(Object.entries(r.element.props).sort())
    )
      throw Error("Unapproved recipe or binding.");
    if (seen.has(r.resource!)) throw Error("Duplicate task or variant.");
    seen.add(r.resource!);
    const parent = positions.get(id)?.parent;
    nodes.push({
      id,
      recipe: r,
      parent: parent?.id ?? null,
      slot: parent?.slot ?? "root",
    });
  }
  const root = nodes.find((n) => n.id === spec.root);
  if (root?.recipe.component !== "Workspace") throw Error("Invalid root.");
  const primary = nodes.filter((n) => n.parent === spec.root && n.slot === "primary");
  if (primary.length !== 1) throw Error("One primary section is required.");
  const has = (component: string, action: number | null = null) =>
    nodes.some((n) => n.recipe.component === component && n.recipe.action === action);
  if (context.objective === "review") {
    if (!has("TransactionReview") || primary[0].recipe.component !== "TransactionReview")
      throw Error("Review is required.");
  } else {
    context.action_types.forEach((_, i) => {
      if (!has("TaskGroup", i) || !has("TaskEditor", i)) throw Error("A task is missing.");
    });
    if (context.objective === "prepare" && primary[0].recipe.component !== "TaskGroup")
      throw Error("Editing must lead.");
    if (
      context.objective !== "prepare" &&
      (!has("DecisionGroup") || !has("BalanceProjection") || primary[0].recipe.component !== "DecisionGroup")
    )
      throw Error("The objective needs a primary projection.");
    if (context.objective === "simulate" && !has("AmountSimulator"))
      throw Error("Simulation needs controls.");
  }
  for (const n of nodes) {
    if (!n.parent) continue;
    const p = nodes.find((p) => p.id === n.parent)!;
    const c = n.recipe.component;
    if (["TaskEditor", "StockEstimate", "RecipientDetails"].includes(c)) {
      if (p.recipe.component !== "TaskGroup" || p.recipe.action !== n.recipe.action || n.slot !== "details")
        throw Error("Task context is misplaced.");
    } else if (c === "BalanceProjection" || c === "AmountSimulator") {
      if (
        p.recipe.component !== "DecisionGroup" ||
        n.slot !== (c === "BalanceProjection" ? "outcome" : "controls")
      )
        throw Error("Decision controls and outcome must stay together.");
    } else if (
      p.id !== spec.root ||
      (["FundingOptions", "AccountInsights"].includes(c) && n.slot !== "context") ||
      (c === "TaskGroup" && !["tasks", "primary"].includes(n.slot))
    )
      throw Error("Invalid group placement.");
  }
  const optional = nodes.filter((n) =>
    ["RecipientDetails", "StockEstimate", "FundingOptions", "AccountInsights"].includes(n.recipe.component),
  );
  if (optional.length > (context.viewport === "mobile" ? 1 : 2)) throw Error("Too much optional context.");
  return nodes;
}
export function fallback(context: Context, reason = "Using a local layout."): Composition {
  const offered = recipes(context),
    spec: Spec = { root: "workspace", elements: {} };
  const add = (
    component: string,
    action: number | null,
    id: string,
    parent?: string,
    slot?: string,
    variant?: string,
  ) => {
    const r = offered.find(
      (r) => r.component === component && r.action === action && (!variant || r.variant === variant),
    )!;
    spec.elements[id] = { ...structuredClone(r.element), children: [] };
    if (parent && slot) {
      const p = spec.elements[parent];
      p.slots ??= {};
      (p.slots[slot] ??= []).push(id);
    }
  };
  add(
    "Workspace",
    null,
    "workspace",
    undefined,
    undefined,
    context.viewport === "mobile" ? "focus" : "split",
  );
  if (context.objective === "review") add("TransactionReview", null, "review", "workspace", "primary");
  else {
    context.action_types.forEach((_, i) => {
      add(
        "TaskGroup",
        i,
        `task${i}`,
        "workspace",
        context.objective === "prepare" && i === 0 ? "primary" : "tasks",
      );
      add(
        "TaskEditor",
        i,
        `editor${i}`,
        `task${i}`,
        "details",
        context.objective === "prepare" ? "full" : "compact",
      );
    });
    if (context.objective !== "prepare") {
      add("DecisionGroup", null, "decision", "workspace", "primary");
      add(
        "BalanceProjection",
        null,
        "projection",
        "decision",
        "outcome",
        context.action_types.length > 1 ? "breakdown" : "summary",
      );
      if (context.objective === "simulate") add("AmountSimulator", null, "simulator", "decision", "controls");
    }
  }
  if (context.financial_context && context.objective !== "review")
    add("AccountInsights", null, "insights", "workspace", "context", "compact");
  return {
    spec,
    nodes: accept(spec, context),
    layout: String(spec.elements.workspace.props.variant),
    source: "fallback",
    reason,
    calls: [],
    elapsedMs: 0,
  };
}
// Simple tasks need model-selected variants, but their legal parent slots are fixed.
export function selectionQuestions(context: Context): Record<string, string[]> {
  const pool = recipes(context);
  const groups = pool.reduce((groups, r) => {
    const group = groups.get(r.resource!) ?? [];
    group.push(r);
    groups.set(r.resource!, group);
    return groups;
  }, new Map<string, Recipe[]>());
  return {
    root: [...pool.filter((r) => r.root).map((r) => r.id), "unavailable"],
    ...Object.fromEntries(
      [...groups.values()].map((group, i) => [`select_${i}`, ["omit", ...group.map((r) => `use:${r.id}`)]]),
    ),
  };
}
export function selectedTree(context: Context, result: Evaluation): Spec {
  const pool = recipes(context),
    groups = [
      ...pool
        .reduce((groups, r) => {
          const group = groups.get(r.resource!) ?? [];
          group.push(r);
          groups.set(r.resource!, group);
          return groups;
        }, new Map<string, Recipe[]>())
        .values(),
    ];
  const root = pool.find((r) => r.root && r.id === result.answers.root?.choice);
  if (!root) throw Error("Invalid root choice.");
  const chosen = [root];
  groups.forEach((group, i) => {
    const choice = result.answers[`select_${i}`]?.choice;
    if (choice === "omit") {
      if (
        !["Workspace", "RecipientDetails", "StockEstimate", "FundingOptions", "AccountInsights"].includes(
          group[0].component,
        )
      )
        throw Error("A required component was omitted.");
      return;
    }
    const recipe = group.find((r) => `use:${r.id}` === choice);
    if (!recipe) throw Error("Invalid component choice.");
    if (!recipe.root) chosen.push(recipe);
  });
  const spec: Spec = { root: root.id, elements: {} };
  for (const r of chosen) spec.elements[r.id] = structuredClone(r.element);
  for (const r of chosen.slice(1)) {
    const taskChild = ["TaskEditor", "RecipientDetails", "StockEstimate"].includes(r.component);
    const parent = taskChild
      ? chosen.find((p) => p.component === "TaskGroup" && p.action === r.action)
      : root;
    if (!parent) throw Error("Missing task group.");
    const slot = taskChild ? "details" : r.component === "TaskGroup" ? "primary" : "context";
    const e = spec.elements[parent.id];
    e.slots ??= {};
    (e.slots[slot] ??= []).push(r.id);
  }
  accept(spec, context);
  return spec;
}

export async function compose(
  context: Context,
  signal: AbortSignal,
  onCall?: () => void,
  auditSession?: string,
): Promise<Composition> {
  const calls: Evaluation[] = [],
    offered = recipes(context),
    started = performance.now();
  try {
    let final: Spec | null = null;
    if (context.objective === "prepare" && context.action_types.length === 1) {
      onCall?.();
      const result = await post(
        "/presentation-v4/evaluate",
        evaluationSchema,
        { context, selected: [], questions: selectionQuestions(context) },
        signal,
        auditSession,
      );
      calls.push(result);
      final = selectedTree(context, result);
      return {
        spec: final,
        nodes: accept(final, context),
        layout: String(final.elements[final.root].props.variant),
        source: "jev",
        reason: null,
        calls,
        elapsedMs: Math.round(performance.now() - started),
      };
    }
    for await (const event of experimental_composeSpec({
      catalog,
      candidates: offered,
      prompt: prompts[context.objective],
      context,
      strategy: "batch",
      maxSteps: 2,
      maxDepth: 3,
      maxElements: 24,
      signal,
      evaluate: async ({ state, questions, signal }) => {
        const selected = Array.isArray(state.selected_elements)
          ? state.selected_elements.map(
              (e: { content: string }) => offered.find((r) => r.description === e.content)?.id,
            )
          : [];
        onCall?.();
        const result = await post(
          "/presentation-v4/evaluate",
          evaluationSchema,
          {
            context,
            selected,
            questions: Object.fromEntries(
              Object.entries(questions).map(([k, q]) => [k, Object.keys(q.criteria)]),
            ),
          },
          signal,
          auditSession,
        );
        calls.push(result);
        return {
          answers: result.answers,
          usage: { inputTokens: result.usage.input_tokens },
        };
      },
    }))
      if (event.type === "complete") {
        if (event.stopReason !== "finish") throw Error("Composition did not finish.");
        final = event.spec;
      }
    const nodes = accept(final, context);
    return {
      spec: final!,
      nodes,
      layout: String(final!.elements[final!.root].props.variant),
      source: "jev",
      reason: null,
      calls,
      elapsedMs: Math.round(performance.now() - started),
    };
  } catch (error) {
    if (signal.aborted) throw error;
    return {
      ...fallback(context, error instanceof Error ? error.message : "Composition unavailable."),
      calls,
      elapsedMs: Math.round(performance.now() - started),
    };
  }
}
