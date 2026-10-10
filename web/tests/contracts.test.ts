import assert from "node:assert/strict";
import { test } from "node:test";
import { draftSchema, understandSchema } from "../src/lib/contracts";
import { exampleDraft } from "../src/lib/examples";
import { estimatedTotal } from "../src/lib/money";

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
