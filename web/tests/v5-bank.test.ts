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
