import assert from "node:assert/strict";
import { test } from "node:test";
import { draftSchema, understandSchema } from "../src/lib/contracts";
import { exampleDraft } from "../src/lib/examples";
import { estimatedTotal } from "../src/lib/money";
import { conversationReducer, initialConversation } from "../src/lib/v2-conversation";
import {
  decidePresentation,
  present,
  bundleSelection,
  composeBundles,
  handoffContext,
} from "../src/lib/v2-presentation";
import { livePresentationSchema } from "../src/lib/v2-presentation";
import { winstonFixtures } from "../src/lib/examples";

test("mock Winston handoff loads structured outputs without parsing and live reply is checked", () => {
  for (const fixture of winstonFixtures) {
    const state = conversationReducer(initialConversation, {
      type: "handoff",
      draft: fixture.draft,
      mode: fixture.mode,
    });
    const selection = present(state).trace.selection;
    const response = {
      source: "jev",
      selection,
      systemone: {
        model: "jev-1.13.0",
        answers: {
          layout: {
            type: "choice",
            choice: selection.layout,
            confidence: 1,
            probabilities: { [selection.layout]: 1 },
          },
        },
      },
    };
    assert.equal(livePresentationSchema.safeParse(response).success, true);
    assert.equal(present(state, response.selection).manifest.source, "jev");
    assert.equal(livePresentationSchema.safeParse({ ...response, key: "should never exist" }).success, false);
    assert.equal(
      livePresentationSchema.safeParse({ ...response, selection: { ...selection, script: "alert(1)" } })
        .success,
      false,
    );
  }
});

test("V2 presentation adapter rejects invented values, missing forms, unavailable timelines and invalid choices", async () => {
  const state = conversationReducer(initialConversation, {
    type: "handoff",
    draft: exampleDraft(0),
    mode: "transaction",
  });
  const base = present(state);
  assert.equal(base.manifest.source, "fallback");
  for (const invalid of [
    { ...base.trace.selection, amount: "999.00" },
    { ...base.trace.selection, layout: "timeline", tools: ["timeline"] },
    { ...base.trace.selection, tools: [] },
    { ...base.trace.selection, primary: 1 },
    { ...base.trace.selection, strategy: "partial" },
    { ...base.trace.selection, supporting: ["projection"] },
  ])
    assert.equal(present(state, invalid).manifest.source, "fallback");
  const selected = await decidePresentation(state, async (request) => {
    assert.equal(JSON.stringify(request).includes("500.00"), false);
    assert.equal(request.options.layout.includes("timeline"), false);
    return { ...base.trace.selection, layout: "split", focus: "recipient" };
  });
  assert.equal(selected.manifest.source, "jev");
  assert.equal(selected.manifest.workspace?.tasks.length, 1);
  assert.match(
    (
      await decidePresentation(state, async () => {
        throw new Error("offline");
      })
    ).trace.fallbackReason!,
    /unavailable/,
  );
});
test("all example action types and multi-action drafts meet the runtime contract", () => {
  for (let i = 0; i < 4; i++)
    assert.equal(draftSchema.parse(exampleDraft(i)).actions.length, i === 3 ? 2 : 1);
});
test("transaction boundary rejects floating money and unknown action types", () => {
  const draft = exampleDraft(0);
  assert.equal(
    draftSchema.safeParse({ ...draft, actions: [{ ...draft.actions[0], amount: 50 }] }).success,
    false,
  );
  assert.equal(
    draftSchema.safeParse({ ...draft, actions: [{ ...draft.actions[0], type: "withdraw" }] }).success,
    false,
  );
  assert.equal(understandSchema.safeParse({ status: "draft", draft }).success, false);
  assert.equal(draftSchema.safeParse({ ...draft, hidden_action: "transfer" }).success, false);
  assert.equal(
    draftSchema.safeParse({ ...draft, actions: [{ ...draft.actions[0], hidden_amount: "10000.00" }] })
      .success,
    false,
  );
});
test("stock estimate uses exact cents even above floating point precision", () => {
  assert.equal(estimatedTotal("227.50", 100), "22750.00");
  assert.equal(estimatedTotal("0.29", 3), "0.87");
  assert.equal(estimatedTotal("9007199254740993.01", 2), "18014398509481986.02");
  assert.throws(() => estimatedTotal("1.001", 2));
  assert.throws(() => estimatedTotal("1.00", 1.5));
});

import { composeWorkspace, validateManifest, selectionSchema, eligibleLayouts } from "../src/lib/composition";
import { editedActions, updateEdit, projectedBalances } from "../src/lib/draft-editor";
import { exampleAccounts, exampleQuote } from "../src/lib/examples";

test("manifest selects from structured facts, validates the registry and cannot omit approvals or actions", () => {
  const draft = exampleDraft(3);
  const facts = { mode: "transaction" as const, accounts: exampleAccounts, scheduled: false };
  const manifest = composeWorkspace(draft, facts);
  assert.equal(manifest.layout, "sequence");
  assert.equal(composeWorkspace(exampleDraft(0), facts).layout, "focus");
  assert.equal(composeWorkspace(draft, { ...facts, mode: "decision" }).layout, "canvas");
  assert.equal(eligibleLayouts(draft, { ...facts, scheduled: true }).includes("timeline"), true);
  assert.throws(() => validateManifest({ ...manifest, tasks: [{ action: 0, priority: 1 }] }, draft, facts));
  assert.throws(() =>
    validateManifest(
      {
        ...manifest,
        tasks: [
          { action: 0, priority: 1 },
          { action: 0, priority: 2 },
        ],
      },
      draft,
      facts,
    ),
  );
  assert.throws(() => validateManifest({ ...manifest, execute: "/api/execute" }, draft, facts));
  assert.throws(() => validateManifest({ ...manifest, layout: "canvas" }, draft, facts));
  assert.throws(() => validateManifest({ ...manifest, supporting: ["projection"] }, draft, facts));
  assert.equal(
    selectionSchema.safeParse({
      mode: "transaction",
      ranks: [{ layout: "html", score: 1 }],
      primary: 0,
      supporting: [],
    }).success,
    false,
  );
  const selection = {
    mode: "transaction",
    ranks: [
      { layout: "comparison", score: 0.9 },
      { layout: "sequence", score: 0.5 },
    ],
    primary: 1,
    supporting: ["accounts"],
  };
  const selected = composeWorkspace(draft, facts, selection, "jev");
  assert.equal(selected.layout, "comparison");
  assert.equal(selected.primary, 1);
  assert.equal(selected.source, "jev");
  assert.throws(() => composeWorkspace(draft, facts, { ...selection, primary: 9 }, "jev"));
});
test("editing stays separate from backend drafts and survives presentation recomposition", () => {
  const draft = exampleDraft(3);
  const edits = updateEdit(updateEdit({}, 0, "amount", "1500.00"), 1, "quantity", "20");
  const result = editedActions(draft, edits, exampleAccounts);
  assert.deepEqual(result.errors, {});
  assert.equal(result.actions[1].type === "stock_order" && result.actions[1].quantity, 20);
  assert.equal(draft.actions[1].type === "stock_order" && draft.actions[1].quantity, 100);
  composeWorkspace(draft, { mode: "transaction", accounts: exampleAccounts, scheduled: false });
  const editedBill = editedActions(draft, edits, exampleAccounts).actions[0];
  assert.equal(editedBill.type !== "stock_order" && editedBill.amount, "1500.00");
  for (const [field, value] of [
    ["quantity", "1.5"],
    ["quantity", "9007199254740992"],
    ["order_type", "limit"],
  ])
    assert.ok(editedActions(draft, updateEdit({}, 1, field, value), exampleAccounts).errors[1]);
  assert.ok(editedActions(draft, updateEdit({}, 0, "amount", "0.00"), exampleAccounts).errors[0]);
  assert.ok(
    editedActions(draft, updateEdit({}, 0, "from_account_id", "acc_7788"), exampleAccounts).errors[0],
  );
  assert.ok(
    editedActions(exampleDraft(0), updateEdit({}, 0, "to_payee_id", "invented"), exampleAccounts).errors[0],
  );
});
test("projections use exact cents and keep currencies and unavailable quotes separate", () => {
  const draft = exampleDraft(3);
  const result = projectedBalances(draft.actions, exampleAccounts, { AAPL: exampleQuote });
  assert.equal(result[0].projected, "1960.00");
  assert.equal(result[1].projected, "7250.00");
  assert.equal(projectedBalances(draft.actions, exampleAccounts, {})[1].projected, null);
  assert.equal(
    projectedBalances(draft.actions, exampleAccounts, { AAPL: { ...exampleQuote, currency: "SGD" } })[1]
      .projected,
    null,
  );
  const actions = editedActions(draft, updateEdit({}, 1, "quantity", "200"), exampleAccounts).actions;
  assert.equal(projectedBalances(actions, exampleAccounts, { AAPL: exampleQuote })[1].projected, "-15500.00");
});

test("complete handoff preserves compatible edits, upstream changes win and approvals reset", () => {
  let state = conversationReducer(initialConversation, {
    type: "handoff",
    draft: exampleDraft(0),
    mode: "transaction",
  });
  state = conversationReducer(state, { type: "edit", index: 0, field: "amount", value: "321.00" });
  state = conversationReducer(state, { type: "edit", index: 0, field: "from_account_id", value: "acc_1234" });
  state = conversationReducer(state, { type: "handoff", draft: exampleDraft(0), mode: "decision" });
  assert.equal(state.edits[0].amount, "321.00");
  state = conversationReducer(state, {
    type: "handoff",
    draft: winstonFixtures.find((f) => f.id === "transfer-amended")!.draft,
    mode: "transaction",
  });
  assert.equal(state.edits[0].amount, undefined);
  assert.equal(state.edits[0].from_account_id, "acc_1234");
  state = conversationReducer(state, { type: "review" });
  state = conversationReducer(state, { type: "approve", index: 0 });
  state = conversationReducer(state, { type: "handoff", draft: state.draft!, mode: "transaction" });
  assert.deepEqual(state.approved, {});
  const bad = { ...exampleDraft(0), actions: [{ ...exampleDraft(0).actions[0], amount: null }] };
  assert.equal(
    conversationReducer(initialConversation, { type: "handoff", draft: bad as never, mode: "transaction" })
      .draft,
    null,
  );
  assert.throws(() => handoffContext(initialConversation, "prepare", "listed"), /resolve/);
  assert.throws(() => composeBundles(initialConversation, "prepare"), /complete/);
});

test("bundles enforce dependent components and retain weak layouts only for unchanged objectives", () => {
  const state = conversationReducer(initialConversation, {
    type: "handoff",
    draft: exampleDraft(0),
    mode: "transaction",
  });
  const previous = {
    objective: "prepare" as const,
    selection: bundleSelection(state, "transfer_editing", "focus", "standard"),
  };
  const selection = bundleSelection(state, "transfer_editing", "split", "standard");
  const reply = livePresentationSchema.parse({
    source: "jev",
    selection,
    systemone: {
      model: "jev-1.13.0",
      answers: {
        bundle: {
          type: "choice",
          choice: "transfer_editing",
          confidence: 1,
          probabilities: { transfer_editing: 1 },
        },
        layout: {
          type: "choice",
          choice: "split",
          confidence: 0.02,
          probabilities: { split: 0.51, focus: 0.49 },
        },
      },
    },
  });
  const stable = composeBundles(state, "prepare", reply, previous);
  assert.equal(stable.manifest.layout, "focus");
  assert.match(stable.bundleTrace.stabilityReason!, /weak/);
  const decision = { ...state, mode: "decision" as const };
  const simulation = bundleSelection(decision, "affordability_simulation", "canvas", "standard");
  const changed = { ...reply, selection: simulation };
  assert.equal(composeBundles(decision, "assess_affordability", changed, previous).manifest.layout, "canvas");
  const invalid = {
    ...changed,
    selection: { ...simulation, supporting: ["accounts"] as typeof simulation.supporting },
  };
  assert.equal(composeBundles(decision, "assess_affordability", invalid).manifest.source, "fallback");
  assert.ok(
    composeBundles(decision, "assess_affordability", invalid).manifest.supporting.includes("projection"),
  );
  assert.equal(
    JSON.stringify(handoffContext(decision, "assess_affordability", "listed", simulation)).includes("500.00"),
    false,
  );
  assert.equal(
    composeBundles(state, "prepare", {
      ...reply,
      selection: { ...selection, bundle: "affordability_simulation" },
    }).manifest.source,
    "fallback",
  );
});
