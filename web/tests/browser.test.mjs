import assert from "node:assert/strict";
import { test } from "node:test";
import { chromium } from "playwright";

const baseURL = "http://127.0.0.1:3000";
const executablePath = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";

test(
  "resolved Winston handoffs compose coherent bundles, preserve edits, reject invalid replies and cancel stale results",
  { timeout: 120000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, reducedMotion: "reduce" });
    let calls = 0,
      failure = false,
      invalid = false,
      delayed = false,
      weak = false;
    let release;
    const requests = [],
      forbidden = [];
    page.on("request", (r) => {
      if (/\/api\/(understand|validate|execute|challenge|device)/.test(r.url())) forbidden.push(r.url());
    });
    await page.route("**/api/presentation-demo", async (route) => {
      calls++;
      const context = route.request().postDataJSON();
      requests.push(context);
      if (delayed)
        await new Promise((resolve) => {
          release = resolve;
        });
      const decision = context.objective === "assess_affordability",
        multi = context.action_types.length > 1;
      const bundle = decision
        ? "affordability_simulation"
        : context.objective === "check_recipient"
          ? "recipient_check"
          : multi
            ? "multi_task_review"
            : "transfer_editing";
      const layout = weak ? "split" : decision ? "focus" : multi ? "sequence" : "focus";
      const selection = {
        bundle,
        layout,
        density: "standard",
        primary: 0,
        strategy: multi ? "multi" : "focused",
        interaction: decision ? "simulate" : "execute",
        focus: decision ? "affordability" : bundle === "recipient_check" ? "recipient" : "amount",
        supporting: decision
          ? ["accounts", "projection"]
          : bundle === "recipient_check"
            ? ["recipient"]
            : ["accounts"],
        tools: decision ? ["form", "slider", "chart"] : ["form"],
      };
      if (invalid) selection.supporting = [];
      await route
        .fulfill({
          status: failure ? 502 : 200,
          json: failure
            ? { error: { message: "JEV unavailable" } }
            : {
                source: "jev",
                selection,
                systemone: {
                  model: "jev-1.13.0",
                  answers: {
                    bundle: { type: "choice", choice: bundle, confidence: 1, probabilities: { [bundle]: 1 } },
                    layout: {
                      type: "choice",
                      choice: layout,
                      confidence: weak ? 0.02 : 1,
                      probabilities: weak ? { split: 0.51, focus: 0.49 } : { [layout]: 1 },
                    },
                  },
                },
              },
        })
        .catch(() => {});
    });
    async function choose(id) {
      await page.getByLabel("Mock Winston output scenario").selectOption(id);
      await page.getByRole("button", { name: "Compose with live JEV" }).click();
      await page.getByText(/Demo data - Live JEV/).waitFor();
    }
    try {
      await page.goto(`${baseURL}/v2/`);
      assert.equal(await page.locator("textarea").count(), 0);
      assert.equal(
        await page.getByLabel("Mock Winston output scenario").locator('option[value="partial"]').count(),
        0,
      );
      assert.equal(calls, 0);
      await choose("transfer");
      await page.getByLabel("Amount for action 1", { exact: true }).fill("321.00");
      assert.equal(calls, 1);
      weak = true;
      await choose("transfer");
      assert.equal(await page.getByLabel("Amount for action 1", { exact: true }).inputValue(), "321.00");
      assert.equal(await page.locator(".v2-layout-focus").count(), 1);
      assert.ok(requests.at(-1).current_ui);
      weak = false;
      await choose("transfer-affordability");
      await page.locator(".v2-support").getByText("SGD 2,879.00", { exact: true }).waitFor();
      assert.ok(
        (await page.locator(".v2-support").boundingBox()).y <
          (await page.locator(".v2-task-grid").boundingBox()).y,
      );
      await page.getByLabel("Simulate amount for action 1").fill("30000");
      assert.equal(await page.getByLabel("Amount for action 1", { exact: true }).inputValue(), "300.00");
      await choose("transfer-recipient");
      await page.getByRole("heading", { name: "Check recipient" }).waitFor();
      assert.equal(await page.getByLabel("Amount for action 1", { exact: true }).inputValue(), "300.00");
      await page.getByLabel("Amount for action 1", { exact: true }).fill("222.00");
      await choose("transfer-amended");
      assert.equal(await page.getByLabel("Amount for action 1", { exact: true }).inputValue(), "300.00");
      failure = true;
      await page.getByRole("button", { name: "Compose with live JEV" }).click();
      await page.getByRole("alert").filter({ hasText: "local fallback" }).waitFor();
      assert.equal(await page.getByLabel("Amount for action 1", { exact: true }).inputValue(), "300.00");
      failure = false;
      invalid = true;
      await page.getByRole("button", { name: "Compose with live JEV" }).click();
      await page.getByRole("alert").filter({ hasText: "Invalid bundle" }).waitFor();
      invalid = false;
      delayed = true;
      await page.getByRole("button", { name: "Compose with live JEV" }).click();
      await page.getByRole("button", { name: "Cancel", exact: true }).click();
      if (release) release();
      assert.equal(await page.getByText(/Demo data - Live JEV/).count(), 0);
      delayed = false;
      await page.getByRole("button", { name: "Review my task" }).click();
      assert.equal(await page.getByRole("button", { name: "Confirm demo task" }).isDisabled(), true);
      await page.getByRole("checkbox", { name: "I have reviewed action 1" }).check();
      await page.getByRole("button", { name: "Confirm demo task" }).click();
      await page.getByText("Review complete. No money moved.").waitFor();
      assert.deepEqual(forbidden, []);
      assert.equal(JSON.stringify(requests).includes("321.00"), false);
      assert.equal(JSON.stringify(requests).includes("John Doe"), false);
    } finally {
      if (release) release();
      await browser.close();
    }
  },
);

test(
  "complete multi-task bundles link costs and projections at desktop and mobile widths",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, reducedMotion: "reduce" });
    await page.route("**/api/presentation-demo", (route) =>
      route.fulfill({ status: 502, json: { error: { message: "Offline demo" } } }),
    );
    try {
      await page.goto(`${baseURL}/v2/`);
      await page.getByLabel("Mock Winston output scenario").selectOption("multi");
      await page.getByRole("button", { name: "Compose with live JEV" }).click();
      await page.getByRole("alert").filter({ hasText: "local fallback" }).waitFor();
      assert.equal(await page.locator(".v2-plan-task").count(), 2);
      assert.equal(await page.locator(".task-card").count(), 0);
      await page.getByLabel("Quantity for action 2").fill("80");
      await page.getByLabel("Impact of action 2").getByText("USD 18,200.00", { exact: true }).waitFor();
      await page.getByLabel("Mock Winston output scenario").selectOption("decision");
      await page.getByRole("button", { name: "Compose with live JEV" }).click();
      await page.locator(".v2-support").getByText("USD 11,800.00", { exact: true }).waitFor();
      assert.equal(await page.getByLabel("Quantity for action 2").inputValue(), "80");
      await page.screenshot({ path: "../graphify-out/v2-bundles-desktop.png", fullPage: true });
      for (const width of [320, 390, 768, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (width === 390)
          await page.screenshot({ path: "../graphify-out/v2-bundles-mobile.png", fullPage: true });
      }
    } finally {
      await browser.close();
    }
  },
);

test(
  "adaptive pages, API clarification, validation, correction and mobile layout",
  { timeout: 120000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
      await page.goto(baseURL);
      await page.getByRole("heading", { level: 1 }).waitFor();
      await page.getByRole("button", { name: "Pay a bill", exact: false }).click();
      await page.getByRole("heading", { name: "Pay your bill", exact: true }).waitFor();
      assert.match(await page.locator(".prototype-notice").innerText(), /Example mode/);
      assert.equal(await page.getByRole("button", { name: "Review example" }).isDisabled(), true);
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      await page.getByRole("checkbox").check();
      await page.getByRole("checkbox").focus();
      await page.keyboard.press("Space");
      assert.equal(await page.getByRole("checkbox").isChecked(), false);
      await page.keyboard.press("Space");
      assert.equal(await page.getByRole("checkbox").isChecked(), true);
      await page.getByRole("button", { name: "Review example" }).click();
      await page.getByText("Example reviewed.", { exact: true }).waitFor();
      await page.getByRole("button", { name: "Change request", exact: true }).click();
      await page.locator(".task-card").waitFor({ state: "hidden" });
      assert.equal(await page.locator(".success-notice").count(), 0);
      await page.getByRole("button", { name: "Two tasks", exact: false }).click();
      await page.getByText("USD 22,750.00", { exact: true }).waitFor();
      assert.equal(await page.locator(".task-card").count(), 2);
      for (const width of [320, 768, 1024, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
          true,
          `Horizontal overflow at ${width}px`,
        );
      }
      await page.setViewportSize({ width: 320, height: 1000 });
      await page.screenshot({ path: "../graphify-out/ui-mobile.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.screenshot({ path: "../graphify-out/ui-desktop.png", fullPage: true });
      await page.getByRole("button", { name: "Start over", exact: true }).click();
      await page.getByLabel("What would you like to do?").fill("send fifty to john");
      await page.getByRole("button", { name: "Build my task" }).click();
      await page.getByRole("heading", { name: "Which John did you mean?" }).waitFor();
      await page.getByRole("button", { name: "John Doe", exact: false }).click();
      await page.getByRole("heading", { name: "Send money", exact: true }).waitFor();
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "Check draft", exact: false }).click();
      await page.getByText("The validation stub returned a match.", { exact: true }).waitFor();
      await page.getByRole("button", { name: "Change request", exact: true }).click();
      assert.equal(await page.locator(".success-notice").count(), 0);
      await page.getByLabel("What would you like to do?").fill("send fifty to john");
      await page.getByRole("button", { name: "Build my task" }).click();
      await page.getByRole("button", { name: "None of these", exact: true }).click();
      await page.getByRole("heading", { name: "Request cancelled", exact: true }).waitFor();
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  },
);

test(
  "cancellation ignores a late draft and malformed responses show an error",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage();
    try {
      await page.route("**/api/understand", async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 600));
        await route.fulfill({ json: { status: "draft", draft: {} } }).catch(() => {});
      });
      await page.goto(baseURL);
      await page.getByLabel("What would you like to do?").fill("send fifty to john");
      await page.getByRole("button", { name: "Build my task" }).click();
      await page.getByRole("button", { name: "Cancel", exact: true }).click();
      await page.waitForTimeout(800);
      assert.equal(await page.locator(".task-card").count(), 0);
      assert.equal(await page.locator(".error-notice").count(), 0);
      await page.getByRole("button", { name: "Build my task" }).click();
      await page.locator(".error-notice").waitFor();
      assert.match(await page.locator(".error-notice").innerText(), /unexpected response/);
    } finally {
      await browser.close();
    }
  },
);

test(
  "microphone sends audio to ASR; abandoning recording sends nothing; mismatches freeze",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({
      executablePath,
      headless: true,
      args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"],
    });
    const context = await browser.newContext({ permissions: ["microphone"] });
    const page = await context.newPage();
    const transcriptions = [];
    page.on("request", (r) => {
      if (r.url().includes("/api/transcribe")) transcriptions.push(r);
    });
    try {
      await page.goto(baseURL);
      await page.getByRole("button", { name: "Use your voice", exact: false }).click();
      await page.getByRole("button", { name: "Stop recording", exact: false }).waitFor();
      await page.waitForTimeout(300);
      await page.getByRole("button", { name: "Start over", exact: true }).click();
      await page.waitForTimeout(300);
      assert.equal(transcriptions.length, 0);
      await page.getByRole("button", { name: "Use your voice", exact: false }).click();
      await page.getByRole("button", { name: "Stop recording", exact: false }).waitFor();
      await page.waitForTimeout(300);
      await page.getByRole("button", { name: "Stop recording", exact: false }).click();
      await page.getByRole("heading", { name: "Which John did you mean?" }).waitFor();
      assert.equal(transcriptions.length, 1);
      assert.match(transcriptions[0].headers()["content-type"], /multipart\/form-data/);
      assert.equal(await page.getByLabel("What would you like to do?").inputValue(), "send fifty to john");
      await page.getByRole("button", { name: "John Doe", exact: false }).click();
      await page.getByRole("heading", { name: "Send money", exact: true }).waitFor();
      await page.route("**/api/validate", (route) =>
        route.fulfill({
          json: {
            status: "mismatch",
            fields: [
              {
                field: "actions[0].amount",
                draft_value: "15.00",
                transcript_says: "fifty",
                note: "amount differs",
              },
            ],
          },
        }),
      );
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "Check draft", exact: false }).click();
      await page.getByRole("heading", { name: "Draft frozen", exact: true }).waitFor();
      assert.equal(await page.locator(".success-notice").count(), 0);
      await page.getByRole("button", { name: "Correct my request", exact: true }).click();
      await page.locator(".task-card").waitFor({ state: "hidden" });
      assert.equal(
        await page.getByLabel("What would you like to do?").evaluate((el) => el === document.activeElement),
        true,
      );
    } finally {
      await browser.close();
    }
  },
);

test(
  "an API stock draft loads its quote and rejects a quote for another ticker",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ viewport: { width: 320, height: 900 } });
    try {
      const fixture = await page.request.post(`${baseURL}/api/understand`, {
        data: {
          user_id: "u_demo",
          session_id: "s_quote",
          answer: { question_id: "q_1", option_id: "pay_4521" },
        },
      });
      const result = await fixture.json();
      result.draft.transcript = "buy 100 shares of Apple";
      result.draft.actions = [
        {
          type: "stock_order",
          from_account_id: "acc_7788",
          from_account_label: "Invest (USD) ?7788",
          side: "buy",
          ticker: "AAPL",
          company_name: "Apple Inc.",
          quantity: 100,
          order_type: "market",
          limit_price: null,
          currency: "USD",
        },
      ];
      await page.route("**/api/understand", (route) => route.fulfill({ json: result }));
      await page.goto(baseURL);
      await page.getByLabel("What would you like to do?").fill("buy 100 shares of Apple");
      const quoteRequest = page.waitForResponse((r) => r.url().includes("/api/quote?ticker=AAPL"));
      await page.getByRole("button", { name: "Build my task" }).click();
      assert.equal((await quoteRequest).status(), 200);
      await page.getByText("USD 22,750.00", { exact: true }).waitFor();
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
        true,
      );
      await page.route("**/api/quote?*", (route) =>
        route.fulfill({
          json: {
            ticker: "MSFT",
            company_name: "Microsoft",
            price: "1.00",
            currency: "USD",
            as_of: "2026-10-10T09:30:00+08:00",
          },
        }),
      );
      await page.getByRole("button", { name: "Refresh details", exact: false }).click();
      await page.getByText("The quote does not match this order.", { exact: true }).waitFor();
      assert.equal(await page.getByText("USD 100.00", { exact: true }).count(), 0);
    } finally {
      await browser.close();
    }
  },
);

test(
  "reduced motion keeps generated cards stable and review controls usable",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ reducedMotion: "reduce" });
    const hydrationErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error" && /hydrat/i.test(message.text())) hydrationErrors.push(message.text());
    });
    try {
      await page.goto(baseURL);
      await page.getByRole("button", { name: "Two tasks", exact: false }).click();
      await page.getByText("USD 22,750.00", { exact: true }).waitFor();
      assert.equal(await page.locator(".task-card").count(), 2);
      await page.locator(".animated-task").first().hover();
      assert.equal(
        await page
          .locator(".animated-task")
          .first()
          .evaluate((node) => getComputedStyle(node).transform),
        "none",
      );
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      while (await page.getByRole("button", { name: /^Approve action/ }).count())
        await page
          .getByRole("button", { name: /^Approve action/ })
          .first()
          .click();
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "Review example" }).click();
      await page.getByText("Example reviewed.", { exact: true }).waitFor();
      assert.equal(
        await page.locator(".success-icon").evaluate((node) => getComputedStyle(node).transform),
        "none",
      );
      assert.deepEqual(hydrationErrors, []);
    } finally {
      await browser.close();
    }
  },
);

test(
  "three compositions preserve edits, recalculate assumptions and never send transactions",
  { timeout: 90000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
    const forbidden = [];
    page.on("request", (request) => {
      if (/\/api\/(execute|challenge|device)/.test(request.url())) forbidden.push(request.url());
    });
    try {
      await page.goto(baseURL);
      await page.getByRole("button", { name: "Send money", exact: false }).click();
      await page.locator('[data-layout="focus"]').waitFor();
      assert.equal(await page.getByLabel("Amount for action 1", { exact: true }).inputValue(), "500.00");
      await page.getByLabel("Amount for action 1", { exact: true }).fill("750.00");
      await page.getByRole("button", { name: "split", exact: true }).click();
      assert.equal(await page.getByLabel("Amount for action 1", { exact: true }).inputValue(), "750.00");
      await page.getByRole("button", { name: "focus", exact: true }).click();
      await page.screenshot({ path: "../graphify-out/scenario-a-desktop.png", fullPage: true });
      await page.getByRole("button", { name: "Two tasks", exact: false }).click();
      await page.locator('[data-layout="sequence"]').waitFor();
      await page.getByRole("button", { name: "Approve action 1", exact: true }).click();
      assert.equal(await page.getByRole("checkbox").isDisabled(), true);
      await page.getByRole("button", { name: "Approve action 2", exact: true }).click();
      assert.equal(await page.getByRole("checkbox").isDisabled(), false);
      await page.getByLabel("Quantity for action 2").fill("50");
      assert.equal(await page.getByRole("checkbox").isDisabled(), true);
      await page.screenshot({ path: "../graphify-out/scenario-b-desktop.png", fullPage: true });
      await page.getByRole("button", { name: "Can I afford it?", exact: false }).click();
      await page.locator('[data-layout="canvas"]').waitFor();
      await page.getByText("USD 7,250.00", { exact: true }).waitFor();
      await page.getByLabel("Quantity for action 2").fill("20");
      await page.getByText("USD 25,450.00", { exact: true }).waitFor();
      await page.getByLabel("Order type for action 2").selectOption("limit");
      assert.equal(
        await page.getByRole("button", { name: "Approve action 2", exact: true }).isDisabled(),
        true,
      );
      await page.getByLabel("Limit price for action 2").fill("200.00");
      await page.getByText("USD 26,000.00", { exact: true }).waitFor();
      await page.getByText("USD 4,000.00", { exact: true }).waitFor();
      await page.getByLabel("Order type for action 2").selectOption("market");
      await page.getByText("USD 25,450.00", { exact: true }).waitFor();
      await page.getByLabel("Amount for action 1", { exact: true }).fill("1500.00");
      await page.getByText("SGD 1,700.00", { exact: true }).waitFor();
      await page.getByRole("button", { name: "comparison", exact: true }).click();
      assert.equal(await page.getByLabel("Quantity for action 2").inputValue(), "20");
      await page.getByRole("button", { name: "canvas", exact: true }).click();
      await page.screenshot({ path: "../graphify-out/scenario-c-desktop.png", fullPage: true });
      for (const [name, layout] of [
        ["Send money", "focus"],
        ["Two tasks", "sequence"],
        ["Can I afford it?", "canvas"],
      ]) {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.getByRole("button", { name, exact: false }).click();
        await page.locator(`[data-layout="${layout}"]`).waitFor();
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        await page.screenshot({ path: `../graphify-out/scenario-${layout}-mobile.png`, fullPage: true });
      }
      assert.deepEqual(forbidden, []);
    } finally {
      await browser.close();
    }
  },
);

test("API field edits invalidate approval and block stale validation", { timeout: 60000 }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  const page = await browser.newPage({ reducedMotion: "reduce" });
  let validations = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/validate")) validations++;
  });
  try {
    await page.goto(baseURL);
    await page.getByLabel("What would you like to do?").fill("send fifty to john");
    await page.getByRole("button", { name: "Build my task" }).click();
    await page.getByRole("button", { name: "John Doe", exact: false }).click();
    await page.getByLabel("Amount for action 1", { exact: true }).fill("80.00");
    await page.getByRole("button", { name: "Approve action 1", exact: true }).click();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Check draft", exact: false }).click();
    await page.getByText(/Edited details need a new backend draft/).waitFor();
    assert.equal(validations, 0);
    await page.getByLabel("Amount for action 1", { exact: true }).fill("0.00");
    assert.equal(
      await page.getByRole("button", { name: "Approve action 1", exact: true }).isDisabled(),
      true,
    );
    assert.equal(await page.getByRole("checkbox").isChecked(), false);
  } finally {
    await browser.close();
  }
});
