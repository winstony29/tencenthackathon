import assert from "node:assert/strict";
import { test } from "node:test";
import { draftSchema, understandSchema } from "../src/lib/contracts";
import { exampleDraft } from "../src/lib/examples";
import { estimatedTotal } from "../src/lib/money";
import { conversationReducer, initialConversation } from "../src/lib/v2-conversation";
import { parseDemoCommand, v2Accounts } from "../src/lib/v2-parser";
import { decidePresentation, present } from "../src/lib/v2-presentation";
import { livePresentationSchema } from "../src/lib/v2-presentation";
import { winstonFixtures } from "../src/lib/examples";

test("mock Winston handoff loads structured outputs without parsing and live reply is checked", () => {
  for (const fixture of winstonFixtures) {
    const state = conversationReducer(initialConversation, {
      type: "load",
      draft: fixture.draft,
      partial: fixture.partial ? { ...fixture.partial, candidates: [...fixture.partial.candidates] } : null,
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

test("V2 clarifies transfer identity, preserves edits through conversation and only confirms after review", () => {
  let state = conversationReducer(initialConversation, { type: "command", text: "Send money to John" });
  assert.equal(state.surface, "partial");
  assert.equal(state.partial?.amount, null);
  assert.equal(state.partial?.recipient, null);
  state = conversationReducer(state, { type: "command", text: "John Smith, $500" });
  assert.equal(state.surface, "workspace");
  assert.equal(state.draft?.actions[0].type, "transfer");
  state = conversationReducer(state, { type: "edit", index: 0, field: "to_payee_id", value: "pay_4521" });
  state = conversationReducer(state, { type: "chat" });
  state = conversationReducer(state, { type: "command", text: "Can I afford that?" });
  assert.equal(present(state).manifest.layout, "canvas");
  state = conversationReducer(state, {
    type: "command",
    text: "Actually, make it $300 and use my savings account",
  });
  assert.equal(state.edits[0].amount, "300.00");
  assert.equal(state.edits[0].to_payee_id, "pay_4521");
  assert.equal(state.edits[0].from_account_id, "acc_1234");
  assert.equal(state.draft?.actions[0].type === "transfer" && state.draft.actions[0].amount, "500.00");
  assert.equal(conversationReducer(state, { type: "finish" }).surface, "workspace");
  state = conversationReducer(state, { type: "review" });
  assert.equal(conversationReducer(state, { type: "finish" }).surface, "review");
  state = conversationReducer(state, { type: "approve", index: 0 });
  assert.equal(conversationReducer(state, { type: "finish" }).surface, "result");
  state = conversationReducer(state, { type: "return" });
  state = conversationReducer(state, { type: "edit", index: 0, field: "amount", value: "0.00" });
  assert.equal(conversationReducer(state, { type: "review" }).surface, "workspace");
});
test("V2 uses general command parsing and explicit demo records rather than prompt matching", () => {
  assert.equal(
    parseDemoCommand("Please send SGD 812.29 to John Smith", 1).draft?.actions[0].type,
    "transfer",
  );
  assert.equal(parseDemoCommand("Transfer USD 500 to John", 1).draft, undefined);
  assert.equal(parseDemoCommand("Send $500 to Jane", 1).draft, undefined);
  assert.equal(
    parseDemoCommand("Pay my credit card bill and buy 37 shares of Apple", 1).draft?.actions.length,
    2,
  );
  assert.equal(parseDemoCommand("Buy 9007199254740993 Apple shares", 1).draft, undefined);
  assert.equal(parseDemoCommand("Buy Apple shares", 1).draft, undefined);
  for (const text of [
    "Buy -100 Apple shares",
    "Buy 1.5 Apple shares",
    "Pay my bill $-500",
    "Pay my bill $500.001",
    "Pay my bill $5,00",
    "Transfer $500 or $300 to John",
  ])
    assert.equal(parseDemoCommand(text, 1).draft, undefined);
  assert.equal(v2Accounts.payees.length, 2);
});
test("V2 presentation adapter rejects invented values, missing forms, unavailable timelines and invalid choices", async () => {
  const state = conversationReducer(initialConversation, {
    type: "command",
    text: "Transfer $500 to John Doe",
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
test("V2 ambiguous multi-task amendments preserve all actions and field edits", () => {
  let state = conversationReducer(initialConversation, {
    type: "command",
    text: "Pay my credit card bill and buy 100 shares of Apple",
  });
  state = conversationReducer(state, { type: "edit", index: 1, field: "quantity", value: "42" });
  const saved = state.edits;
  state = conversationReducer(state, { type: "command", text: "Actually make it $300" });
  assert.equal(state.surface, "refinement");
  assert.equal(state.edits, saved);
  state = conversationReducer(state, { type: "command", text: "Make the bill amount $300" });
  assert.equal(state.edits[0].amount, "300.00");
  assert.equal(state.edits[1].quantity, "42");
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
