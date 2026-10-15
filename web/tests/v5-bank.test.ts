import assert from "node:assert/strict";
import { test } from "node:test";
import { checkChallenge } from "../src/lib/v5-bank";
import { exampleDraft } from "../src/lib/examples";

test("V5 signs only an unexpired bank payload for the exact displayed draft", () => {
  const draft = exampleDraft(0);
  const value = {
    nonce: "n_test",
    signing_payload: JSON.stringify(draft) + ".n_test",
    expires_at: new Date(Date.now() + 60000).toISOString(),
  };
  assert.equal(checkChallenge(value, draft).signing_payload, value.signing_payload);
  assert.throws(() => checkChallenge({ ...value, expires_at: "2020-01-01T00:00:00Z" }, draft), /expired/);
  assert.throws(
    () => checkChallenge({ ...value, signing_payload: "<canonical draft JSON>.n_test" }, draft),
    /not ready/,
  );
  assert.throws(() => checkChallenge({ ...value, nonce: "n_other" }, draft));
  assert.throws(() => checkChallenge(value, { ...draft, user_id: "u_other" }), /different/);
  assert.throws(() => checkChallenge(value, { ...draft, actions: [] }), /different/);
});


import { acceptPlanView, fallbackPlanView } from "../src/components/v5-enquiry";

test("Conditional manifests cannot drop dependencies, duplicate tasks or bind unknown data", () => {
  const view = fallbackPlanView(2, true);
  assert.equal(acceptPlanView(view, 2, true).nodes.length, 6);
  for (let i = 0; i < view.nodes.length; i++) {
    if (view.nodes[i].component === "QuoteMetric") continue;
    assert.throws(() => acceptPlanView({...view, nodes: view.nodes.filter((_, j) => i !== j)}, 2, true));
  }
  assert.throws(() => acceptPlanView({...view, nodes: [...view.nodes, view.nodes[0]]}, 2, true));
  assert.throws(() => acceptPlanView({...view, nodes: view.nodes.map(n => n.component === "PlannedAction" ? {...n, binding: "99"} : n)}, 2, true));
  assert.throws(() => acceptPlanView(view, 2, false));
});


test("demo conditions remain part of the signed draft", () => {
  const now = Date.now();
  const condition = {ticker: "MSFT", operator: "lte" as const, threshold: "300.00", currency: "USD" as const};
  const draft = {...exampleDraft(0), condition};
  const challenge = {nonce: "n_test", signing_payload: JSON.stringify(draft) + ".n_test", expires_at: new Date(now+60000).toISOString()};
  assert.equal(checkChallenge(challenge, draft).nonce, "n_test");
  assert.throws(() => checkChallenge(challenge, {...draft, condition: {...condition, threshold: "400.00"}}));
  assert.throws(() => checkChallenge(challenge, exampleDraft(0)));
});
