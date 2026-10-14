import { financialRows, decimal } from "../src/lib/v4-financial";
import assert from "node:assert/strict";
import { test } from "node:test";
import { initialState, reducer, effective } from "../src/lib/v4-state";
import { demoHandoff } from "../src/lib/v4-demo";
import { contextFor } from "../src/lib/v4-catalog";
import { accept, fallback } from "../src/lib/v4-composition";

const start = () =>
  reducer(initialState, { type: "handoff", value: demoHandoff("transfer", 0, initialState) });
test("V4 keeps hypothetical amounts separate until explicit adoption", () => {
  let s = start();
  s = reducer(s, { type: "handoff", value: demoHandoff("transfer", 2, s) });
  assert.equal((effective(s).actions[0] as { amount: string }).amount, "500.00");
  assert.equal((effective(s, true).actions[0] as { amount: string }).amount, "700.00");
  s = reducer(s, { type: "apply" });
  assert.equal((effective(s).actions[0] as { amount: string }).amount, "700.00");
  assert.equal(s.handoff?.objective, "prepare");
});
test("V4 preserves raw edits but accepts explicit upstream amendments", () => {
  let s = reducer(start(), { type: "edit", index: 0, field: "amount", value: "650." });
  s = reducer(s, { type: "handoff", value: { ...s.handoff, objective: "assess_affordability" } });
  assert.equal(s.edits[0].amount, "650.");
  s = reducer(s, { type: "handoff", value: demoHandoff("transfer", 3, s) });
  assert.equal((effective(s).actions[0] as { amount: string }).amount, "300.00");
});
test("V4 rejects unresolved handoffs and blocks invalid drafts and unacknowledged review", () => {
  const s = start();
  assert.match(reducer(s, { type: "handoff", value: { draft: null, partial: {} } }).error, /Winston/);
  let bad = reducer(s, { type: "edit", index: 0, field: "amount", value: "-5" });
  bad = reducer(bad, { type: "objective", value: "review" });
  assert.equal(bad.handoff?.objective, "prepare");
  let review = reducer(s, { type: "objective", value: "review" });
  assert.equal(reducer(review, { type: "finish" }).done, false);
  review = reducer(review, { type: "approve", index: 0 });
  assert.equal(reducer(review, { type: "finish" }).done, true);
});
test("V4 validates complete typed data rather than fixed fixture identities", () => {
  const h = demoHandoff("transfer", 0, initialState);
  h.data.accounts.accounts[0].account_id = "custom";
  h.draft.actions[0].from_account_id = "custom";
  h.data.financial!.accounts[0].account_id = "custom";
  assert.equal(reducer(initialState, { type: "handoff", value: h }).error, "");
  const first = h.draft.actions[0];
  if (first.type === "transfer") first.currency = "USD";
  assert.match(reducer(initialState, { type: "handoff", value: h }).error, /consistent/);
});
test("V4 rejects missing objective content, wrong bindings, duplicate tasks and invalid trees", () => {
  const ctx = { ...contextFor(start(), "desktop"), objective: "assess_affordability" as const };
  const good = fallback(ctx).spec;
  assert.ok(accept(good, ctx).length >= 5);
  const missing = structuredClone(good);
  delete missing.elements.projection;
  missing.elements.decision.slots!.outcome = [];
  assert.throws(() => accept(missing, ctx), /projection/);
  const binding = structuredClone(good);
  binding.elements.editor0.props.binding = "arbitrary";
  assert.throws(() => accept(binding, ctx));
  const duplicate = structuredClone(good);
  duplicate.elements.extra = structuredClone(duplicate.elements.editor0);
  duplicate.elements.task0.slots!.details.push("extra");
  assert.throws(() => accept(duplicate, ctx), /Duplicate/);
  const cycle = structuredClone(good);
  cycle.elements.task0.slots!.details.push("workspace");
  assert.throws(() => accept(cycle, ctx));
});
test("V4 supplies required content for every objective and both viewport classes", () => {
  for (const journey of ["transfer", "multi"] as const)
    for (const viewport of ["mobile", "desktop"] as const)
      for (const objective of ["prepare", "assess_affordability", "simulate", "review"] as const) {
        const s = reducer(initialState, { type: "handoff", value: demoHandoff(journey, 0, initialState) });
        const ctx = { ...contextFor(s, viewport), objective };
        const result = fallback(ctx);
        assert.ok(accept(result.spec, ctx).length);
        assert.equal(
          result.nodes.filter((n) => n.recipe.component === "TaskEditor").length,
          objective === "review" ? 0 : s.handoff!.draft.actions.length,
        );
      }
});

test("V4 subtracts future bills once, keeps history separate, and distinguishes unknown", () => {
  const s = start();
  const row = financialRows(s)[0];
  assert.equal(row.remaining, 270000n);
  assert.equal(row.afterBills, 144000n);
  assert.equal(contextFor(s, "desktop").reserve, "breached");
  const absent = structuredClone(s);
  delete absent.handoff!.data.financial;
  assert.equal(financialRows(absent)[0].afterBills, null);
  assert.equal(contextFor(absent, "desktop").commitments, "unknown");
  s.handoff!.data.financial!.accounts[0].commitments!.bills = [];
  assert.equal(contextFor(s, "desktop").commitments, "none");
  assert.equal(financialRows(s)[0].afterBills, 270000n);
});
test("V4 validates context references and avoids double counting an explicitly linked bill", () => {
  const h = demoHandoff("multi", 0, initialState);
  h.data.financial!.accounts[0].commitments!.bills = [
    { id: "card", label: "Card", due: "2026-10-20", amount: "1240.00", covered_by_draft_action: 0 },
  ];
  const s = reducer(initialState, { type: "handoff", value: h });
  assert.equal(s.error, "");
  assert.equal(financialRows(s)[0].afterBills, 196000n);
  const partialPayment = reducer(s, { type: "edit", index: 0, field: "amount", value: "1000.00" });
  assert.equal(financialRows(partialPayment)[0].committed, 24000n);
  assert.equal(financialRows(partialPayment)[0].afterBills, 196000n);
  h.data.financial!.accounts[0].account_id = "missing";
  assert.ok(reducer(initialState, { type: "handoff", value: h }).error);
});
test("V4 financial signals change with valid edits; no money or identity goes into context", () => {
  const s = reducer(start(), { type: "edit", index: 0, field: "amount", value: "4000.00" });
  const ctx = contextFor(s, "desktop");
  assert.equal(ctx.funding, "shortfall");
  assert.ok(!JSON.stringify(ctx).includes("4000"));
  assert.equal(decimal(-1n), "-0.01");
});

test("simple composition uses one gateway call and preserves a validated task tree", async () => {
  const { compose, selectionQuestions } = await import("../src/lib/v4-composition");
  const ctx = contextFor(start(), "desktop");
  const original = globalThis.fetch;
  let count = 0;
  globalThis.fetch = async (_url, init) => {
    count++;
    const body = JSON.parse(String(init?.body));
    assert.deepEqual(body.selected, []);
    assert.deepEqual(body.questions, selectionQuestions(ctx));
    const answers = Object.fromEntries(
      Object.entries(body.questions as Record<string, string[]>).map(([key, options]) => [
        key,
        { choice: key === "root" ? options[0] : options.find((o) => o.startsWith("use:"))!, confidence: 1 },
      ]),
    );
    return Response.json({
      answers,
      model: "jev-test",
      latency_ms: 10,
      usage: { input_tokens: 1, output_tokens: 1 },
    });
  };
  try {
    const result = await compose(ctx, new AbortController().signal);
    assert.equal(count, 1);
    assert.equal(result.source, "jev");
    assert.equal(result.calls.length, 1);
    assert.equal(accept(result.spec, ctx).filter((n) => n.recipe.component === "TaskEditor").length, 1);
  } finally {
    globalThis.fetch = original;
  }
});

test("single-call tree refuses missing mandatory fields and unapproved choices", async () => {
  const { selectionQuestions, selectedTree } = await import("../src/lib/v4-composition");
  const ctx = contextFor(start(), "desktop");
  const answers = Object.fromEntries(
    Object.entries(selectionQuestions(ctx)).map(([key, options]) => [
      key,
      { choice: key === "root" ? options[0] : "omit", confidence: 1 },
    ]),
  );
  const result = { answers, model: "jev-test", latency_ms: 1, usage: { input_tokens: 1, output_tokens: 1 } };
  assert.throws(() => selectedTree(ctx, result), /required/);
  result.answers.root.choice = "unapproved";
  assert.throws(() => selectedTree(ctx, result), /root/);
});
