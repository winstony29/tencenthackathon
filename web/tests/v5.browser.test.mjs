import assert from "node:assert/strict";
import { test } from "node:test";
import { chromium } from "playwright";

const executablePath = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
function response(body) {
  const { selected, context, questions } = body;
  return {
    model: "jev-test",
    latency_ms: 1,
    usage: { input_tokens: 1, output_tokens: 1 },
    answers: Object.fromEntries(
      Object.entries(questions).map(([k, options]) => {
        let choice = options[0];
        if (k === "root")
          choice = context.viewport === "mobile" ? "Workspace_all_focus" : "Workspace_all_split";
        if (k.startsWith("select_")) {
          const use = options.find((o) => o.startsWith("use:"));
          choice = /Workspace|RecipientDetails|StockEstimate|FundingOptions/.test(use) ? "omit" : use;
        }
        if (k.startsWith("parent_")) {
          const i = Number(k.split("_").at(-1));
          const r = selected[i],
            action = r.split("_")[1];
          let parent = 0,
            slot = "primary";
          if (/TaskEditor|RecipientDetails|StockEstimate/.test(r)) {
            parent = selected.findIndex((s) => s.startsWith(`TaskGroup_${action}_`));
            slot = "details";
          } else if (/BalanceProjection|AmountSimulator/.test(r)) {
            parent = selected.findIndex((s) => s.startsWith("DecisionGroup_"));
            slot = r.startsWith("BalanceProjection") ? "outcome" : "controls";
          } else if (r.startsWith("AccountInsights")) slot = "context";
          else if (r.startsWith("TaskGroup"))
            slot = context.objective === "prepare" && action === "0" ? "primary" : "tasks";
          choice = `node_${parent}:${slot}`;
        }
        if (k.startsWith("order_")) choice = k.split("_").at(-1);
        assert.ok(options.includes(choice), `${k}: ${choice}`);
        return [k, { choice, confidence: 1 }];
      }),
    ),
  };
}
import { webcrypto } from "node:crypto";

// Scripted intent engine and read-only bank data, in the CONTRACTS v0.4 shapes.
const accounts = {
  accounts: [
    { account_id: "acc_1234", label: "Savings …1234", type: "savings", currency: "SGD", balance: "3200.00" },
    {
      account_id: "acc_7788",
      label: "Invest (USD) …7788",
      type: "investment",
      currency: "USD",
      balance: "30000.00",
    },
  ],
  payees: [
    { payee_id: "pay_4521", name: "John Doe", label: "John Doe …4521" },
    { payee_id: "pay_8892", name: "John Smith", label: "John Smith …8892" },
  ],
  billers: [
    { biller_id: "bil_0001", name: "DBS Credit Card", label: "DBS Credit Card …9012", amount_due: "1240.00" },
  ],
  holdings: [{ ticker: "AAPL", quantity: 10 }],
};
const meta = { latency_ms: 5, model: "jev-fake", source: "fake", phrasings: 1, cached: false };
const savings = { from_account_id: "acc_1234", from_account_label: "Savings …1234" };
const transfer = {
  type: "transfer",
  ...savings,
  to_payee_id: "pay_4521",
  to_payee_label: "John Doe …4521",
  amount: "50.00",
  currency: "SGD",
};
const bill = {
  type: "bill_payment",
  ...savings,
  biller_id: "bil_0001",
  biller_label: "DBS Credit Card …9012",
  bill_reference: "4120-XXXX-XXXX-9012",
  amount: "1240.00",
  currency: "SGD",
};
const shares = {
  type: "stock_order",
  from_account_id: "acc_7788",
  from_account_label: "Invest (USD) …7788",
  side: "buy",
  ticker: "AAPL",
  company_name: "Apple Inc.",
  quantity: 100,
  order_type: "market",
  limit_price: null,
  currency: "USD",
};
function understand(body, sessions) {
  const draftReply = (actions, transcript, objective = "prepare") => ({
    status: "draft",
    draft: {
      draft_id: `d_${body.session_id.slice(2, 8)}`,
      user_id: "u_demo",
      session_id: body.session_id,
      transcript,
      created_at: "2026-10-13T09:30:00+08:00",
      actions,
      evidence: [],
    },
    objective,
    scenario: null,
    meta,
  });
  const said = body.transcript ?? "";
  if (body.answer?.option_id === "pay_4521")
    return (sessions[body.session_id] = draftReply([transfer], "Send $50 to John"));
  if (said === "Can I afford that?" && sessions[body.session_id])
    return { ...sessions[body.session_id], objective: "assess_affordability" };
  if (said === "Send $50 to John")
    return {
      status: "question",
      question: {
        question_id: "q_1",
        text: "Which John did you mean?",
        options: accounts.payees.map((p) => ({ option_id: p.payee_id, label: p.label })),
        allow_none: true,
      },
      evidence: [],
      meta,
    };
  if (said === "Pay my card bill and buy 100 Apple shares") return draftReply([bill, shares], said);
  if (/ignore previous instructions/i.test(said))
    return {
      status: "blocked",
      reason: "possible_injection",
      message: "This request contains instructions I can't act on. Please say it again.",
      meta,
    };
  return {
    status: "unsupported",
    message: "I can help with transfers, bill payments and stock orders.",
    meta,
  };
}

async function setup(page, mode = "otp", beforeComposition = async () => {}) {
  const calls = [];
  const sessions = {};
  let draft, publicKey, payload;
  await page.route("**/api/understand", (route) =>
    route.fulfill({ json: understand(route.request().postDataJSON(), sessions) }),
  );
  await page.route("**/api/accounts?*", (route) => route.fulfill({ json: accounts }));
  await page.route("**/api/presentation-v4/evaluate", async (route) => {
    await beforeComposition();
    return route.fulfill({ json: response(route.request().postDataJSON()) });
  });
  await page.route("**/api/validate", async (route) => {
    calls.push("validate");
    draft = route.request().postDataJSON().draft;
    await route.fulfill({
      json:
        mode === "mismatch"
          ? {
              status: "mismatch",
              fields: [
                {
                  field: "actions[0].amount",
                  draft_value: "50.00",
                  transcript_says: "500",
                  note: "Mismatch",
                },
              ],
            }
          : { status: "match", receipt: "vr_test" },
    });
  });
  await page.route("**/api/device/register", async (route) => {
    calls.push("register");
    publicKey = route.request().postDataJSON().public_key_jwk;
    await route.fulfill({ json: { status: "registered" } });
  });
  await page.route("**/api/challenge", async (route) => {
    calls.push("challenge");
    assert.deepEqual(route.request().postDataJSON().draft, draft);
    payload = JSON.stringify(draft) + ".n_test";
    await route.fulfill({
      json: {
        nonce: "n_test",
        signing_payload: payload,
        expires_at: new Date(Date.now() + (mode === "expired" ? -1000 : 300000)).toISOString(),
      },
    });
  });
  await page.route("**/api/execute", async (route) => {
    calls.push("execute");
    const body = route.request().postDataJSON();
    assert.deepEqual(Object.keys(body).sort(), ["nonce", "otp", "signature"]);
    const key = await webcrypto.subtle.importKey(
      "jwk",
      publicKey,
      { name: "ECDSA", namedCurve: "P-256" },
      false,
      ["verify"],
    );
    assert.ok(
      await webcrypto.subtle.verify(
        { name: "ECDSA", hash: "SHA-256" },
        key,
        Buffer.from(body.signature, "base64url"),
        new TextEncoder().encode(payload),
      ),
      "signature must cover exact bank payload",
    );
    if (mode === "lost") {
      await route.abort();
      return;
    }
    await route.fulfill({
      json:
        mode === "reject"
          ? { status: "rejected", reason: "insufficient_funds", message: "Not enough funds." }
          : mode === "otp" && body.otp === null
            ? { status: "needs_extra_check", method: "otp", message: "Enter your one-time code." }
            : {
                status: "executed",
                results: draft.actions.map((_, i) => ({ action_index: i, reference: `TX-${i}` })),
              },
    });
  });
  await page.goto(`${process.env.APP_URL ?? "http://127.0.0.1:3000"}/v5/`);
  await page.getByRole("button", { name: "Choose a banking request" }).click();
  await page.getByRole("button", { name: "Send $50 to John", exact: true }).click();
  assert.equal(calls.length, 0);
  await page.getByRole("button", { name: /John Doe/ }).click();
  await page.waitForSelector('.v5-card-content[data-source="jev"]');
  return calls;
}

test(
  "V5 compact cards confirm directly with real WebCrypto and optional OTP",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      const calls = await setup(page);
      assert.equal(await page.getByRole("checkbox").count(), 0);
      assert.equal(await page.getByText("Composition details").count(), 0);
      assert.equal(await page.getByRole("button", { name: /Review/ }).count(), 0);
      assert.match(await page.locator(".v5-figure").innerText(), /50\.00/);
      await page.screenshot({ path: `${process.env.TEMP}/v5-clean-mobile.png`, fullPage: true });
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        assert.ok(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          `overflow at ${width}`,
        );
      }
      await page.screenshot({ path: `${process.env.TEMP}/v5-clean-desktop.png`, fullPage: true });
      await page.getByRole("button", { name: "Transfer now" }).click();
      await page.getByRole("textbox", { name: "One-time code" }).waitFor();
      assert.deepEqual(calls, ["validate", "register", "challenge", "execute"]);
      await page.getByRole("textbox", { name: "One-time code" }).fill("123456");
      await page.getByRole("button", { name: "Confirm code" }).click();
      await page.getByRole("heading", { name: "Request completed" }).waitFor();
      assert.deepEqual(calls, ["validate", "register", "challenge", "execute", "execute"]);
      assert.equal(await page.getByRole("button", { name: "Transfer now" }).count(), 0);
      assert.equal(await page.getByRole("button", { name: "Can I afford it?" }).count(), 0);
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  },
);

test(
  "V5 stops on mismatch or expired bank challenge, and handles rejected or unknown outcomes",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      for (const mode of ["mismatch", "expired", "reject", "lost"]) {
        const page = await browser.newPage({ reducedMotion: "reduce" });
        const calls = await setup(page, mode);
        await page.getByRole("button", { name: "Transfer now" }).click();
        await page.locator(".v5-surface").getByRole("alert").waitFor();
        assert.equal(
          calls.includes("execute"),
          mode === "reject" || mode === "lost",
          `${mode}: ${await page.locator(".v5-surface").getByRole("alert").innerText()} ${calls}`,
        );
        if (mode === "lost") assert.ok(await page.getByRole("button", { name: "Transfer now" }).isDisabled());
        if (mode === "mismatch") assert.deepEqual(calls, ["validate"]);
        await page.close();
      }
    } finally {
      await browser.close();
    }
  },
);

test(
  "V5 affordability shows a compact projection without extra review tasks",
  { timeout: 30000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      const page = await browser.newPage({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
      const calls = await setup(page);
      await page.getByRole("button", { name: "Can I afford it?" }).click();
      await page.getByRole("region", { name: "Balance projection" }).waitFor();
      assert.equal(await page.locator(".v5-task").count(), 1);
      assert.equal(calls.length, 0, "looking at affordability never calls the bank");
      await page.getByRole("button", { name: "Transfer now" }).waitFor();
      await page.screenshot({ path: `${process.env.TEMP}/v5-clean-afford.png`, fullPage: true });
    } finally {
      await browser.close();
    }
  },
);
test("V5 keeps both requested actions and confirms the complete draft once", { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce" });
    const calls = await setup(page, "success");
    await page.route("**/api/quote?*", (route) =>
      route.fulfill({
        json: {
          ticker: "AAPL",
          company_name: "Apple Inc.",
          price: "227.50",
          currency: "USD",
          as_of: "2026-10-13T09:00:00+08:00",
        },
      }),
    );
    await page.getByRole("button", { name: "Start over", exact: true }).click();
    await page.getByRole("button", { name: "Choose a banking request" }).click();
    await page
      .getByRole("button", { name: "Pay my card bill and buy 100 Apple shares", exact: true })
      .click();
    await page.getByRole("button", { name: "Confirm 2 actions" }).waitFor();
    assert.equal(await page.locator(".v5-task").count(), 2);
    await page.getByText("4120-XXXX-XXXX-9012").waitFor();
    await page.getByText("US$22,750.00").waitFor();
    await page.getByRole("button", { name: "Confirm 2 actions" }).evaluate((button) => {
      button.click();
      button.click();
    });
    await page.getByRole("heading", { name: "Request completed" }).waitFor();
    assert.equal(calls.filter((c) => c === "execute").length, 1);
    await page.getByText("TX-0").waitFor();
    await page.getByText("TX-1").waitFor();
  } finally {
    await browser.close();
  }
});

test(
  "V5 shows the engine's blocked and unsupported replies and sends nothing to the bank",
  { timeout: 30000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      const page = await browser.newPage({ reducedMotion: "reduce" });
      const calls = await setup(page);
      await page.getByRole("button", { name: "Start over", exact: true }).click();
      await page.getByRole("button", { name: "Choose a banking request" }).click();
      await page.getByRole("button", { name: "Try a blocked request", exact: true }).click();
      await page.getByRole("heading", { name: "Request stopped." }).waitFor();
      await page.getByText("instructions I can't act on").waitFor();
      await page.getByRole("button", { name: "Start over", exact: true }).first().click();
      await page.getByRole("button", { name: "Choose a banking request" }).click();
      await page.getByLabel("What would you like to do?").fill("what's the weather");
      await page.getByRole("button", { name: "Send request" }).click();
      await page.getByRole("heading", { name: "Nothing was sent." }).waitFor();
      assert.deepEqual(calls, []);
    } finally {
      await browser.close();
    }
  },
);


test("V5 shows resolved task before composition and disables payment until ready", { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce" });
    let compositions = 0;
    await setup(page, "success", async () => {
      compositions++;
      await page.locator(".v5-task").waitFor();
      assert.equal(await page.getByRole("button", { name: "Transfer now" }).isDisabled(), true);
      assert.match(await page.locator(".v5-card-content [role=status]").innerText(), /Preparing/);
    });
    assert.equal(compositions, 1);
    assert.equal(await page.getByRole("button", { name: "Transfer now" }).isEnabled(), true);
  } finally { await browser.close(); }
});
