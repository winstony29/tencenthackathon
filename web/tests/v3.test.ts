import assert from "node:assert/strict";
import { test } from "node:test";
import { candidates, makeContext, offeredQuestions, type Context } from "../src/lib/v3-registry";
import { compose, validateManifest } from "../src/lib/v3-presentation";
import { demoHandoff, effective, initialState, reducer, scenarioActions } from "../src/lib/v3-state";
import { exampleDraft, exampleQuote } from "../src/lib/examples";
import { v2Accounts } from "../src/lib/v2-parser";
import { projectedBalances } from "../src/lib/draft-editor";

const context = (objective: Context["objective"] = "prepare", multi = false) =>
  makeContext(exampleDraft(multi ? 3 : 0), objective, v2Accounts, { AAPL: exampleQuote });
function reply(ctx: Context, choices: Record<string, string> = {}) {
  return {
    source: "jev",
    model: "jev-test",
    latency_ms: 12,
    answers: Object.fromEntries(
      Object.entries(offeredQuestions(ctx)).map(([name, options]) => [
        name,
        {
          type: "choice",
          choice: choices[name] ?? options[0],
          confidence: 1,
          probabilities: Object.fromEntries(
            options.map((o) => [o, o === (choices[name] ?? options[0]) ? 1 : 0]),
          ),
        },
      ]),
    ),
  };
}
test("V3 catalog gates unsupported context and keeps the primary aligned with Winston", () => {
  for (const multi of [false, true])
    for (const objective of ["prepare", "assess_affordability", "simulate", "review"] as const) {
      const ctx = context(objective, multi),
        eligible = candidates(ctx);
      assert.ok(!eligible.some((c) => c.component === "account_comparison"));
      for (const primary of offeredQuestions(ctx).primary)
        for (const layout of offeredQuestions(ctx).layout)
          for (const include of ["include", "omit"]) {
            const choices = Object.fromEntries(
              Object.keys(offeredQuestions(ctx))
                .filter((k) => k.startsWith("show_"))
                .map((k) => [k, include]),
            );
            const result = compose(ctx, reply(ctx, { ...choices, primary, layout }));
            assert.equal(result.manifest.source, "jev");
            assert.equal(result.rejected, null);
            assert.equal(result.manifest.nodes.filter((n) => n.slot === "primary")[0].key, primary);
            const editors = result.manifest.nodes.filter((n) => n.action !== null);
            assert.equal(editors.length, objective === "review" ? 0 : multi ? 2 : 1);
            assert.equal(new Set(editors.map((n) => n.action)).size, editors.length);
          }
    }
  assert.deepEqual(offeredQuestions(context("simulate")).primary, ["amount_simulator"]);
  assert.deepEqual(offeredQuestions(context("assess_affordability")).primary, ["balance_projection"]);
  const ctx = context();
  ctx.available_context = ctx.available_context.filter((c) => c !== "recipients");
  assert.ok(!candidates(ctx).some((c) => c.component === "recipient_check"));
});
test("V3 rejects malformed answers and impossible manifests with a complete fallback", () => {
  const ctx = context(),
    good = reply(ctx);
  for (const mutation of [
    { ...good, answers: { ...good.answers, primary: { ...good.answers.primary, choice: "execute_money" } } },
    { ...good, answers: {} },
    { ...good, secret: "extra" },
    { ...good, answers: { ...good.answers, primary: { ...good.answers.primary, confidence: NaN } } },
  ]) {
    const result = compose(ctx, mutation);
    assert.equal(result.manifest.source, "fallback");
    assert.ok(result.manifest.nodes.some((n) => n.component === "transfer_editor"));
  }
  const manifest = compose(ctx, good).manifest;
  assert.throws(() => validateManifest({ ...manifest, nodes: [...manifest.nodes, manifest.nodes[0]] }, ctx));
  assert.throws(() => validateManifest({ ...manifest, nodes: [] }, ctx));
  assert.throws(() =>
    validateManifest({ ...manifest, nodes: manifest.nodes.map((n) => ({ ...n, action: 3 })) }, ctx),
  );
  assert.throws(() =>
    validateManifest(
      { ...manifest, nodes: manifest.nodes.map((n) => ({ ...n, props: { amount: "1.00" } })) },
      ctx,
    ),
  );
  assert.throws(() => validateManifest({ ...manifest, layout: "generated_jsx" }, ctx));
});

test("recipient emphasis never removes the other banking tasks", () => {
  const draft = structuredClone(exampleDraft(0));
  draft.actions.push(...exampleDraft(3).actions);
  const ctx = makeContext(draft, "check_recipient", v2Accounts, { AAPL: exampleQuote });
  const result = compose(ctx, reply(ctx));
  assert.equal(result.manifest.nodes.find((n) => n.slot === "primary")?.component, "recipient_check");
  assert.deepEqual(
    result.manifest.nodes.filter((n) => n.action !== null).map((n) => n.action),
    [0, 1, 2],
  );
});
test("V3 preserves only eligible same-objective composition", () => {
  const ctx = context(),
    prior = compose(ctx, reply(ctx, { layout: "split" })).manifest;
  ctx.current_ui = {
    primary: prior.nodes[0].key,
    components: prior.nodes.map((n) => n.key),
    layout: prior.layout,
    density: prior.density,
  };
  const held = compose(ctx, reply(ctx, { continuity: "preserve", layout: "focus" }), {
    context: ctx,
    manifest: prior,
  });
  assert.equal(held.manifest.layout, "split");
  assert.ok(held.stability);
  const changed = context("assess_affordability");
  const next = compose(changed, reply(changed), { context: ctx, manifest: prior });
  assert.equal(next.manifest.nodes.find((n) => n.slot === "primary")?.component, "balance_projection");
  assert.equal(next.stability, null);
});
test("V3 scenarios stay separate, cannot enter review, and apply only explicitly", () => {
  let state = reducer(initialState, { type: "handoff", value: demoHandoff("transfer", 0, initialState) });
  state = reducer(state, { type: "edit", index: 0, field: "amount", value: "321.00" });
  state = reducer(state, { type: "handoff", value: demoHandoff("transfer", 2, state) });
  assert.equal((effective(state).actions[0] as { amount: string }).amount, "321.00");
  assert.equal((scenarioActions(state).actions[0] as { amount: string }).amount, "700.00");
  assert.equal(
    projectedBalances(scenarioActions(state).actions, v2Accounts, { AAPL: exampleQuote })[0].projected,
    "2500.00",
  );
  const review = reducer(state, { type: "review" });
  assert.equal((effective(review).actions[0] as { amount: string }).amount, "321.00");
  assert.deepEqual(review.scenario, {});
  assert.equal(reducer(review, { type: "finish" }).task.surface, "review");
  const approved = reducer(review, { type: "approve", index: 0 });
  assert.equal(reducer(approved, { type: "finish" }).task.surface, "result");
  const applied = reducer(state, { type: "apply" });
  assert.equal((effective(applied).actions[0] as { amount: string }).amount, "700.00");
  assert.deepEqual(applied.task.approved, {});
  const amended = reducer(state, { type: "handoff", value: demoHandoff("transfer", 3, state) });
  assert.equal((effective(amended).actions[0] as { amount: string }).amount, "300.00");
  assert.deepEqual(amended.scenario, {});
});
test("V3 invalid scenarios and incomplete handoffs do not fabricate usable values", () => {
  let state = reducer(initialState, { type: "handoff", value: demoHandoff("transfer", 2, initialState) });
  state = reducer(state, { type: "scenario", index: 0, field: "amount", value: "-3" });
  assert.ok(Object.keys(scenarioActions(state).errors).length);
  assert.equal(reducer(state, { type: "apply" }), state);
  const draft = structuredClone(exampleDraft(0));
  delete (draft.actions[0] as Partial<(typeof draft.actions)[0]>).from_account_id;
  const rejected = reducer(state, { type: "handoff", value: { draft, objective: "prepare" } });
  assert.equal(rejected.task, state.task);
  assert.ok(rejected.error);
});
test("V3 multi-task scenarios retain currency boundaries and private context", () => {
  let state = reducer(initialState, { type: "handoff", value: demoHandoff("multi", 0, initialState) });
  state = reducer(state, { type: "handoff", value: demoHandoff("multi", 2, state) });
  const rows = projectedBalances(scenarioActions(state).actions, v2Accounts, { AAPL: exampleQuote });
  assert.deepEqual(
    rows.map((r) => [r.currency, r.projected]),
    [
      ["SGD", "1960.00"],
      ["USD", "11800.00"],
    ],
  );
  assert.equal((effective(state).actions[1] as { quantity: number }).quantity, 100);
  const ctx = JSON.stringify(context("assess_affordability", true));
  for (const secret of ["acc_1234", "John", "1240.00", "30000.00", "227.50", "transcript", "draft_id"])
    assert.ok(!ctx.includes(secret));
});
