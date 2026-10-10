import assert from "node:assert/strict";
import { test } from "node:test";
import { chromium } from "playwright";

const baseURL = "http://127.0.0.1:3000";
const executablePath = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";

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
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "Review example" }).click();
      await page.getByText("Example reviewed.", { exact: true }).waitFor();
      assert.equal(
        await page.locator(".success-icon").evaluate((node) => getComputedStyle(node).transform),
        "none",
      );
    } finally {
      await browser.close();
    }
  },
);
