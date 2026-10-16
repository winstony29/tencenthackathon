import assert from "node:assert/strict";
import { test } from "node:test";
import { tmpdir } from "node:os";
import { chromium } from "playwright";

const executablePath =
  process.env.CHROME_PATH ??
  (process.platform === "win32"
    ? "C:/Program Files/Google/Chrome/Application/chrome.exe"
    : process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : undefined);
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
  if (said === "What's my balance?")
    return { status: "answer", kind: "balance", accounts: accounts.accounts, meta };
  if (said === "What's my credit score?") return { status: "answer", kind: "credit_score", score: 1, meta };
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
  let draft, publicKey, payload, firstPayment;
  await page.route("**/api/understand", (route) =>
    route.fulfill({ json: understand(route.request().postDataJSON(), sessions) }),
  );
  await page.route("**/api/presentation-v5/answer", (route) =>
    route.fulfill({
      json: {
        source: "jev",
        variant: "list",
        detail: "expanded",
        summary: false,
        latency_ms: 1,
      },
    }),
  );
  await page.route("**/api/accounts?*", (route) => route.fulfill({ json: accounts }));
  await page.route("**/api/presentation-v4/evaluate", async (route) => {
    await beforeComposition();
    return route.fulfill({ json: response(route.request().postDataJSON()) });
  });
  await page.route("**/api/audit/presentation", (route) => route.fulfill({ json: { status: "recorded" } }));
  await page.route("**/api/validation/cancel", (route) => route.fulfill({ json: { status: "cancelled" } }));
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
    if (mode === "lost" && !firstPayment) {
      firstPayment = body;
      await route.abort();
      return;
    }
    if (mode === "lost") assert.deepEqual(body, firstPayment, "recovery must reuse the signed payment");
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
  await page.getByRole("button", { name: /^JD John Doe/ }).click();
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
      await page.screenshot({ path: `${tmpdir()}/v5-clean-mobile.png`, fullPage: true });
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        assert.ok(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          `overflow at ${width}`,
        );
      }
      await page.screenshot({ path: `${tmpdir()}/v5-clean-desktop.png`, fullPage: true });
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
        if (mode !== "mismatch") await page.getByRole("button", { name: "Transfer now" }).click();
        else assert.equal(await page.getByRole("button", { name: "Transfer now" }).isDisabled(), true);
        await page.locator(".v5-surface").getByRole("alert").first().waitFor();
        assert.equal(
          calls.includes("execute"),
          mode === "reject" || mode === "lost",
          `${mode}: ${await page.locator(".v5-surface").getByRole("alert").first().innerText()} ${calls}`,
        );
        if (mode === "lost") {
          assert.ok(await page.getByRole("button", { name: "Cancel", exact: true }).isDisabled());
          await page.getByRole("button", { name: "Check status", exact: true }).click();
          await page.getByRole("heading", { name: "Request completed" }).waitFor();
          assert.equal(calls.filter((c) => c === "challenge").length, 1);
          assert.equal(calls.filter((c) => c === "execute").length, 2);
        }
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
      assert.ok(
        calls.every((c) => c === "validate"),
        "inspection may audit but never authorizes or executes",
      );
      await page.getByRole("button", { name: "Transfer now" }).waitFor();
      await page.screenshot({ path: `${tmpdir()}/v5-clean-afford.png`, fullPage: true });
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
    await page.locator(".v5-validation").getByText("Request checked", { exact: true }).waitFor();
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
      assert.ok(calls.every((c) => c === "validate"));
    } finally {
      await browser.close();
    }
  },
);

test(
  "V5 shows resolved task before composition and disables payment until ready",
  { timeout: 30000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      const page = await browser.newPage({ reducedMotion: "reduce" });
      let compositions = 0;
      await setup(page, "success", async () => {
        compositions++;
        await page.locator(".v5-task").waitFor();
        assert.equal(await page.getByRole("button", { name: "Transfer now" }).isDisabled(), true);
        assert.match(await page.locator(".v5-card-content [role=status]").first().innerText(), /Preparing/);
      });
      assert.equal(compositions, 1);
      await page.getByText("Request checked", { exact: true }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Transfer now" }).isEnabled(), true);
    } finally {
      await browser.close();
    }
  },
);
test(
  "V5 shows read-only answers with nothing to sign, and unknown kinds as unsupported",
  { timeout: 30000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      const page = await browser.newPage({ reducedMotion: "reduce" });
      const calls = await setup(page);
      for (const [text, expected] of [
        ["What's my balance?", "Your balances"],
        ["What's my credit score?", "Nothing was sent."],
      ]) {
        await page.getByRole("button", { name: "Start over", exact: true }).first().click();
        await page.getByRole("button", { name: "Choose a banking request" }).click();
        await page.getByLabel("What would you like to do?").fill(text);
        await page.getByRole("button", { name: "Send request" }).click();
        if (expected === "Your balances") {
          await page.getByRole("region", { name: expected }).waitFor();
          await page.getByText("S$3,200.00").waitFor();
          assert.equal(await page.getByRole("button", { name: /now|Confirm/ }).count(), 0);
        } else await page.getByRole("heading", { name: expected }).waitFor();
      }
      assert.ok(calls.every((c) => c === "validate"));
    } finally {
      await browser.close();
    }
  },
);

test("V5 preserves filtered bill answers through timeline composition", { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce", viewport: { width: 390, height: 844 } });
    await setup(page);
    const forbidden = [];
    await page.route("**/api/account-insights?*", (r) => {
      forbidden.push("insights");
      return r.abort();
    });
    await page.route("**/api/presentation-v5/enquiry", (r) => {
      assert.equal(r.request().postDataJSON().topic, "request");
      return r.abort();
    });
    await page.route("**/api/understand", (r) =>
      r.fulfill({
        json: {
          status: "answer",
          kind: "bills",
          meta,
          bills: [
            {
              label: "Selected rent",
              amount: "1450.00",
              currency: "SGD",
              due_at: "2026-11-01T09:00:00+08:00",
              account_id: "acc_1234",
              account_label: "Savings",
              recurrence: "monthly",
            },
          ],
        },
      }),
    );
    await page.route("**/api/presentation-v5/answer", async (r) => {
      const input = r.request().postDataJSON();
      assert.equal(input.kind, "bills");
      assert.equal(input.count, 1);
      assert.equal(input.timeline_requested, true);
      assert.equal(input.context_request, undefined); // Covered answer stays on the structured path.
      await page.getByText(/Selected rent/).waitFor(); // visible before model completion
      await r.fulfill({
        json: { source: "jev", variant: "timeline", detail: "expanded", summary: false, latency_ms: 1 },
      });
    });
    await page.getByRole("button", { name: "Start over", exact: true }).first().click();
    await page.getByRole("button", { name: "Choose a banking request" }).click();
    await page.getByLabel("What would you like to do?").fill("Show my rent as a timeline");
    await page.getByRole("button", { name: "Send request" }).click();
    await page.locator('.v5-answer[data-variant="timeline"]').waitFor();
    assert.equal(await page.locator(".v5-timeline-stop").count(), 1);
    assert.match(await page.locator(".v5-answer").innerText(), /monthly/);
    assert.equal(await page.getByRole("button", { name: /Transfer now|Pay now|Confirm/ }).count(), 0);
    assert.deepEqual(forbidden, []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  } finally {
    await browser.close();
  }
});

test(
  "pending and unavailable audits cannot authorize; a late result cannot restore a cancelled task",
  { timeout: 30000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      const page = await browser.newPage({ reducedMotion: "reduce" });
      const calls = await setup(page, "success");
      await page.getByText("Request checked", { exact: true }).waitFor();
      await page.route("**/api/understand", (r) =>
        r.fulfill({
          json: understand({ ...r.request().postDataJSON(), answer: { option_id: "pay_4521" } }, {}),
        }),
      );
      let release;
      const hold = new Promise((resolve) => {
        release = resolve;
      });
      let entered;
      const started = new Promise((resolve) => {
        entered = resolve;
      });
      await page.route("**/api/validate", async (route) => {
        entered();
        await hold;
        await route.fulfill({ json: { status: "match", receipt: "old-receipt" } }).catch(() => {});
      });
      await page.getByRole("button", { name: "Start over", exact: true }).first().click();
      await page.getByRole("button", { name: "Choose a banking request" }).click();
      await page.getByLabel("What would you like to do?").fill("send 500 to John Doe");
      await page.getByRole("button", { name: "Send request" }).click();
      await started;
      await page.getByRole("button", { name: "Transfer now" }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Transfer now" }).isDisabled(), true);
      await page.getByRole("button", { name: "Start over", exact: true }).first().click();
      release();
      await page.getByRole("button", { name: "Choose a banking request" }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Transfer now" }).count(), 0);
      await page.route("**/api/validate", (r) =>
        r.fulfill({ json: { status: "unavailable", message: "Check unavailable" } }),
      );
      await page.getByRole("button", { name: "Choose a banking request" }).click();
      await page.getByLabel("What would you like to do?").fill("send 500 to John Doe");
      await page.getByRole("button", { name: "Send request" }).click();
      await page.getByRole("button", { name: "Retry check" }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Transfer now" }).isDisabled(), true);
      assert.ok(calls.every((c) => c === "validate"));
    } finally {
      await browser.close();
    }
  },
);

for (const width of [390, 1024, 1440]) {
  test(
    `conditional plan preserves meaning and never offers payment at ${width}px`,
    { timeout: 30000 },
    async () => {
      const browser = await chromium.launch({ executablePath, headless: true });
      try {
        const page = await browser.newPage({ viewport: { width, height: 1000 }, reducedMotion: "reduce" });
        const forbidden = [];
        await page.route("**/api/validate", (r) => {
          forbidden.push("validate");
          return r.abort();
        });
        await page.route("**/api/execute", (r) => {
          forbidden.push("execute");
          return r.abort();
        });
        await page.route("**/api/audit/presentation", (r) => r.fulfill({ status: 204 }));
        const plan = {
          request: "If AAPL is below US$300, send John Doe S$50",
          condition: {
            text: "If AAPL is below US$300",
            ticker: "AAPL",
            operator: "lt",
            threshold: "300.00",
            currency: "USD",
            state: "stale",
            quote: {
              ticker: "AAPL",
              company_name: "Apple Inc.",
              price: "227.50",
              currency: "USD",
              as_of: "2026-10-12T09:30:00+08:00",
            },
          },
          actions: [{ ...transfer, amount: "50.00" }],
          timing: "now",
          unresolved: [],
          executable: false,
          limitation: "Conditional payments aren't available yet. Nothing will be sent.",
          intent: {
            request: "If AAPL is below a threshold send an amount to [recipient]",
            conversation: [],
            tasks: ["transfer"],
            constraints: ["stock_price"],
          },
        };
        await page.route("**/api/understand", (r) => r.fulfill({ json: { status: "plan", plan, meta } }));
        await page.route("**/api/presentation-v5/plan", async (r) => {
          assert.equal(r.request().postDataJSON().plan.executable, false);
          await page.getByRole("region", { name: "Condition", exact: true }).waitFor();
          // A malformed selection must keep the full fallback plan.
          await r.fulfill({ json: { source: "jev", layout: "flow", nodes: [] } });
        });
        await page.goto(`${process.env.APP_URL ?? "http://127.0.0.1:3000"}/v5/`);
        await page.getByRole("button", { name: "Choose a banking request" }).click();
        await page.getByLabel("What would you like to do?").fill(plan.request);
        await page.getByRole("button", { name: "Send request" }).click();
        await page.getByText("Quote needs updating", { exact: true }).waitFor();
        assert.match(await page.locator(".v5-plan").innerText(), /US\$300/);
        assert.match(await page.locator(".v5-plan").innerText(), /S\$50/);
        assert.match(await page.locator(".v5-plan").innerText(), /Nothing will be sent/);
        assert.equal(await page.getByRole("button", { name: /Transfer now|Confirm|Pay now/ }).count(), 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        assert.deepEqual(forbidden, []);
        await page.screenshot({ path: `${tmpdir()}/jev-conditional-${width}.png`, fullPage: true });
      } finally {
        await browser.close();
      }
    },
  );
}

test("one UI call adds context without a coverage payment gate", { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 900 }, reducedMotion: "reduce" });
    const calls = await setup(page, "success");
    await page.getByText("Request checked", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Start over", exact: true }).first().click();
    await page.route("**/api/understand", (r) => {
      const result = understand({ ...r.request().postDataJSON(), answer: { option_id: "pay_4521" } }, {});
      const extra = /spending/.test(r.request().postDataJSON().transcript ?? "");
      result.presentation = {
        request: "Transfer",
        conversation: [],
        tasks: ["transfer"],
        constraints: [],
        coverage: extra ? "partial" : "complete",
        gaps: extra ? ["spending"] : [],
      };
      return r.fulfill({ json: result });
    });
    let compositions = 0;
    const enquiries = [];
    await page.route("**/api/presentation-v5/enquiry", (r) => {
      enquiries.push(1);
      return r.abort();
    });
    await page.route("**/api/presentation-v4/evaluate", (r) => {
      compositions++;
      const input = r.request().postDataJSON();
      assert.match(input.context_request.request, /spending/);
      assert.equal(input.context_request.outcome, "draft");
      return r.fulfill({
        json: {
          ...response(input),
          context_view: {
            topic: "request",
            source: "jev",
            latency_ms: 1,
            data: {
              source: "synthetic",
              as_of: "2026-10-14T00:00:00Z",
              accounts: [],
              transactions: null,
              spending: [],
              forecasts: [],
            },
            spec: {
              root: "workspace",
              elements: {
                workspace: { type: "EnquiryWorkspace", variant: "focus", children: ["context"] },
                context: { type: "PeriodComparison", variant: "compact", children: [] },
              },
            },
          },
        },
      });
    });
    await page.getByRole("button", { name: "Choose a banking request" }).click();
    await page.getByLabel("What would you like to do?").fill("Send John S$50 and show monthly spending");
    await page.getByRole("button", { name: "Send request" }).click();
    await page.getByRole("heading", { name: "Month comparison" }).waitFor();
    await page.getByText("Request checked", { exact: true }).waitFor();
    await page.waitForFunction(() =>
      [...document.querySelectorAll("button")].some(
        (b) => b.textContent.includes("Transfer now") && !b.disabled,
      ),
    );
    assert.equal(await page.getByText(/Part of your request still needs checking/).count(), 0);
    assert.equal(compositions, 1);
    assert.deepEqual(enquiries, []);
    // A gateway failure falls back to the ordinary task UI, not a payment veto.
    await page.getByRole("button", { name: "Start over", exact: true }).first().click();
    await page.route("**/api/presentation-v4/evaluate", (r) => {
      assert.ok(!r.request().postDataJSON().context_request);
      return r.fulfill({ status: 502, json: { detail: "Unavailable" } });
    });
    await page.getByRole("button", { name: "Choose a banking request" }).click();
    await page.getByLabel("What would you like to do?").fill("Send John S$50");
    await page.getByRole("button", { name: "Send request" }).click();
    await page.getByText("Request checked", { exact: true }).waitFor();
    await page.waitForFunction(() =>
      [...document.querySelectorAll("button")].some(
        (b) => b.textContent.includes("Transfer now") && !b.disabled,
      ),
    );
    assert.equal(await page.getByText(/Part of your request still needs checking/).count(), 0);
    assert.deepEqual(enquiries, []);

    assert.ok(!calls.includes("execute"));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  } finally {
    await browser.close();
  }
});

test("demo condition stays prepared without polling price", { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce", viewport: { width: 390, height: 900 } });
    await setup(page, "success");
    await page.getByRole("button", { name: "Start over", exact: true }).first().click();
    let price = "250.00";
    await page.route("**/api/quote?*", (r) =>
      r.fulfill({
        json: {
          ticker: "MSFT",
          company_name: "Microsoft",
          price,
          currency: "USD",
          as_of: new Date().toISOString(),
        },
      }),
    );
    await page.route("**/api/understand", (r) => {
      const result = understand({ ...r.request().postDataJSON(), answer: { option_id: "pay_4521" } }, {});
      result.draft.condition = { ticker: "MSFT", operator: "lt", threshold: "300.00", currency: "USD" };
      return r.fulfill({ json: result });
    });
    await page.getByRole("button", { name: "Choose a banking request" }).click();
    await page
      .getByLabel("What would you like to do?")
      .fill("If Microsoft is under US$300, send John S$50 now");
    await page.getByRole("button", { name: "Send request" }).click();
    await page.getByText("Condition met when prepared", { exact: true }).waitFor();
    await page.waitForFunction(() =>
      [...document.querySelectorAll("button")].some(
        (b) => b.textContent.includes("Transfer now") && !b.disabled,
      ),
    );
    price = "350.00";
    await page.waitForTimeout(11000);
    assert.equal(await page.getByRole("button", { name: "Transfer now" }).isDisabled(), false);
    await page.getByText("Condition met when prepared", { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  } finally {
    await browser.close();
  }
});

test(
  "V5 records the orb tap as a 16 kHz WAV and sends the transcript as the request",
  { timeout: 30000 },
  async () => {
    const browser = await chromium.launch({
      executablePath,
      headless: true,
      args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
    });
    try {
      const page = await browser.newPage({ reducedMotion: "reduce", permissions: ["microphone"] });
      const errors = [];
      page.on("pageerror", (e) => errors.push(e.message));
      const sessions = {};
      let wav;
      await page.route("**/api/transcribe", async (route) => {
        const body = route.request().postDataBuffer();
        assert.match(route.request().headers()["content-type"], /^multipart\/form-data; boundary=/);
        assert.match(body.toString("latin1"), /name="audio"; filename="speech.wav"/);
        const start = body.indexOf("RIFF");
        wav = {
          riff: body.toString("latin1", start, start + 4),
          wave: body.toString("latin1", start + 8, start + 12),
          format: body.readUInt16LE(start + 20),
          channels: body.readUInt16LE(start + 22),
          rate: body.readUInt32LE(start + 24),
          bits: body.readUInt16LE(start + 34),
          bytes: body.readUInt32LE(start + 40),
        };
        await route.fulfill({
          json: {
            transcript: "Send $50 to John",
            confidence: null,
            speaker: { status: "enrolled", score: null },
          },
        });
      });
      await page.route("**/api/understand", (route) =>
        route.fulfill({ json: understand(route.request().postDataJSON(), sessions) }),
      );
      await page.goto(`${process.env.APP_URL ?? "http://127.0.0.1:3000"}/v5/`);
      await page.getByRole("button", { name: "Speak your request" }).click();
      await page.getByRole("button", { name: "Listening, tap to stop" }).waitFor();
      await page.waitForTimeout(1500);
      await page.getByRole("button", { name: "Listening, tap to stop" }).click();
      await page.getByRole("heading", { name: "Which John did you mean?" }).waitFor();
      assert.deepEqual(
        { ...wav, bytes: undefined },
        { riff: "RIFF", wave: "WAVE", format: 1, channels: 1, rate: 16000, bits: 16, bytes: undefined },
      );
      assert.ok(wav.bytes >= 16000 * 2 * 0.3, `only ${wav.bytes} bytes of audio`); // server minimum 0.3 s
      assert.match(await page.locator(".v5-heard p").innerText(), /Send \$50 to John/);
      await page.getByText("Voice enrolled for this demo").waitFor();
      // A spoken clarify answer goes through the same send() path, so the mic is beside the answer box.
      assert.equal(await page.getByRole("button", { name: "Speak instead" }).count(), 1);
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  },
);

test(
  "V5 refuses a voice that is not the enrolled speaker and sends nothing (D-054)",
  { timeout: 30000 },
  async () => {
    const browser = await chromium.launch({
      executablePath,
      headless: true,
      args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
    });
    try {
      const page = await browser.newPage({ reducedMotion: "reduce", permissions: ["microphone"] });
      const forbidden = [];
      await page.route("**/api/transcribe", (route) =>
        route.fulfill({
          json: { transcript: "", confidence: null, speaker: { status: "rejected", score: 0.31 } },
        }),
      );
      await page.route("**/api/understand", (route) => {
        forbidden.push("understand");
        return route.abort();
      });
      await page.goto(`${process.env.APP_URL ?? "http://127.0.0.1:3000"}/v5/`);
      await page.getByRole("button", { name: "Speak your request" }).click();
      await page.getByRole("button", { name: "Listening, tap to stop" }).waitFor();
      await page.waitForTimeout(800);
      await page.getByRole("button", { name: "Listening, tap to stop" }).click();
      await page.getByRole("heading", { name: "Nothing was sent." }).waitFor();
      await page.getByText("That doesn't sound like you").waitFor();
      assert.deepEqual(forbidden, []);
      await page.getByRole("button", { name: "Start over" }).first().click();
      await page.getByRole("button", { name: "Speak your request" }).waitFor();
    } finally {
      await browser.close();
    }
  },
);

test("V5 enrols a voice sample through the same recorder path (D-054)", { timeout: 30000 }, async () => {
  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
  });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce", permissions: ["microphone"] });
    let posted = false;
    await page.route("**/api/voiceprint/enrol", (route) => {
      posted = route.request().postDataBuffer().indexOf("RIFF") >= 0;
      return route.fulfill({ json: { clips: 3 } });
    });
    await page.goto(`${process.env.APP_URL ?? "http://127.0.0.1:3000"}/v5/`);
    await page.getByRole("button", { name: "Choose a banking request" }).click();
    await page.getByRole("button", { name: "Enrol my voice" }).click();
    await page.getByRole("button", { name: "Listening, tap to stop" }).waitFor();
    await page.waitForTimeout(800);
    await page.getByRole("button", { name: "Listening, tap to stop" }).click();
    await page.getByText("Voice sample 3 added").waitFor();
    assert.equal(posted, true);
    assert.equal(await page.getByRole("button", { name: "Enrol my voice" }).count(), 1); // back in choose
  } finally {
    await browser.close();
  }
});
