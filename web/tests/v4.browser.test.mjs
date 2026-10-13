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
test("V4 ignores cancelled work and recovers from a malformed model reply", { timeout: 30000 }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce" });
    let slow = false,
      malformed = false;
    await page.route("**/api/presentation-v4/evaluate", async (route) => {
      const body = route.request().postDataJSON();
      if (slow) await new Promise((resolve) => setTimeout(resolve, 700));
      await route.fulfill({ json: malformed ? { answers: {} } : response(body) }).catch(() => {});
    });
    await page.goto("http://127.0.0.1:3000/v4/");
    await page.waitForSelector('.v4-grid[data-source="jev"]');
    slow = true;
    await page.getByRole("button", { name: /Can I afford that/ }).click();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Transfer", exact: true }).click();
    await page.waitForTimeout(1000);
    assert.equal(await page.locator('[data-component="BalanceProjection"]').count(), 0);
    assert.equal(await page.locator('[data-component="TaskEditor"]').count(), 1);
    slow = false;
    malformed = true;
    await page.getByRole("button", { name: /Can I afford that/ }).click();
    await page.getByRole("button", { name: "Try live layout" }).waitFor();
    assert.equal(await page.locator('[data-component="BalanceProjection"]').count(), 1);
    assert.equal(await page.locator('[data-component="TaskEditor"]').count(), 1);
    malformed = false;
    await page.getByRole("button", { name: "Try live layout" }).click();
    await page.waitForSelector('.v4-grid[data-source="jev"]');
  } finally {
    await browser.close();
  }
});
test(
  "V4 composes, preserves focus and raw edits, isolates scenarios and requires review",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    try {
      const page = await browser.newPage({
        viewport: { width: 1440, height: 1000 },
        reducedMotion: "reduce",
      });
      const errors = [],
        requests = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.route("**/api/presentation-v4/evaluate", async (route) => {
        const body = route.request().postDataJSON();
        requests.push(body);
        await new Promise((resolve) => setTimeout(resolve, 120));
        await route.fulfill({ json: response(body) });
      });
      await page.goto("http://127.0.0.1:3000/v4/");
      await page.waitForSelector('.v4-grid[data-source="jev"]');
      const amount = page.getByRole("textbox", { name: "Amount for task 1", exact: true });
      await amount.fill("625.");
      const count = requests.length;
      await page.waitForTimeout(250);
      assert.equal(requests.length, count, "edits must not trigger JEV");
      await page.locator(".v4-inspector summary").click();
      await page.getByRole("button", { name: "Recompose", exact: true }).click();
      await amount.focus();
      await amount.evaluate((n) => {
        window.v4Input = n;
        n.setSelectionRange(2, 2);
      });
      await page.waitForFunction(() =>
        document.querySelector(".v4-status").textContent.includes("Workspace ready"),
      );
      assert.equal(await amount.inputValue(), "625.");
      assert.equal(
        await amount.evaluate(
          (n) => n === window.v4Input && document.activeElement === n && n.selectionStart === 2,
        ),
        true,
      );
      await page.getByRole("button", { name: /What if I send/ }).click();
      await page.waitForSelector('[data-component="AmountSimulator"]');
      await page.waitForFunction(() =>
        document.querySelector(".v4-status").textContent.includes("Workspace ready"),
      );
      assert.equal(await amount.inputValue(), "625.");
      assert.equal(await page.getByRole("button", { name: /Review transfer/ }).isDisabled(), true);
      await amount.fill("625.00");
      await page.getByRole("button", { name: /Review transfer/ }).click();
      await page.waitForSelector('[data-component="TransactionReview"]');
      assert.equal(await page.getByRole("button", { name: "Confirm demo" }).isDisabled(), true);
      assert.match(await page.locator('[data-component="TransactionReview"]').innerText(), /625/);
      await page.getByRole("checkbox").check();
      await page.getByRole("button", { name: "Confirm demo" }).click();
      await page.getByRole("heading", { name: "Demo complete." }).waitFor();
      await page.getByRole("button", { name: "Pay & invest", exact: true }).click();
      await page.waitForSelector('.v4-grid[data-source="jev"]');
      assert.equal(await page.locator('[data-component="TaskEditor"]').count(), 2);
      const insights = page.locator('[data-component="AccountInsights"]');
      await insights.waitFor();
      assert.match(await insights.innerText(), /Rent/);
      await insights.locator("summary").click();
      assert.match(await insights.innerText(), /Historical spending is not deducted again/);
      for (const width of [768, 390, 320]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.waitForTimeout(350);
        assert.ok(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          `overflow at ${width}`,
        );
      }
      await page.screenshot({ path: process.env.TEMP + "/v4-financial-mobile.png", fullPage: true });
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  },
);
