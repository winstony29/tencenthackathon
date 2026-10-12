import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { chromium } from "playwright";

const catalog = JSON.parse(readFileSync(new URL("../presentation-catalog.json", import.meta.url), "utf8"));
const executablePath = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
function answer(context) {
  const items = Object.entries(catalog).flatMap(([id, c]) => {
    if (
      !c.objectives.includes(context.objective) ||
      !c.data.every((d) => context.available_context.includes(d))
    )
      return [];
    const matches = context.action_types.flatMap((t, i) => (c.types.includes(t) ? [i] : []));
    return !matches.length
      ? []
      : (c.perAction ? matches : [null]).map((i) => ({
          key: id + (i === null ? "" : `:${i}`),
          primary: c.primaryFor.includes(context.objective),
          required: c.requiredFor.includes(context.objective),
        }));
  });
  const options = {
    primary: items.filter((c) => c.primary).map((c) => c.key),
    layout: context.action_types.length > 1 ? ["split", "sequence"] : ["focus", "split"],
    density: ["compact", "comfortable"],
    interaction:
      context.objective === "review"
        ? ["review"]
        : context.objective === "simulate"
          ? ["adjust"]
          : context.objective === "prepare"
            ? ["edit", "inspect"]
            : ["inspect"],
    continuity: context.current_ui ? ["preserve", "recompose"] : ["recompose"],
  };
  items
    .filter((c) => !c.required)
    .forEach((c) => {
      options[`show_${c.key}`] = ["include", "omit"];
    });
  return {
    source: "jev",
    model: "jev-test",
    latency_ms: 24,
    answers: Object.fromEntries(
      Object.entries(options).map(([key, opts]) => {
        const choice =
          key === "primary" && context.objective === "simulate"
            ? "amount_simulator"
            : key.startsWith("show_")
              ? "omit"
              : key === "layout" && context.objective !== "prepare"
                ? "split"
                : key === "density"
                  ? "comfortable"
                  : opts[0];
        return [
          key,
          {
            type: "choice",
            choice,
            confidence: 1,
            probabilities: Object.fromEntries(opts.map((o) => [o, o === choice ? 1 : 0])),
          },
        ];
      }),
    ),
  };
}
test(
  "V3 transfer morphs with separate scenarios, explicit review and edit-stale cancellation",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
      reducedMotion: "reduce",
    });
    const requests = [],
      errors = [];
    let delayed = false,
      invalid = false,
      fail = false,
      release;
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (/\/api\/(understand|execute|validate|challenge)/.test(r.url()))
        errors.push("Unexpected banking call");
    });
    await page.route("**/api/presentation-v3", async (route) => {
      const context = route.request().postDataJSON();
      requests.push(context);
      if (delayed)
        await new Promise((resolve) => {
          release = resolve;
        });
      const payload = answer(context);
      if (invalid) payload.answers.primary.choice = "unknown_component";
      await route
        .fulfill({
          status: fail ? 502 : 200,
          contentType: "application/json",
          body: JSON.stringify(payload),
        })
        .catch(() => {});
    });
    const turn = async (label) => {
      await page.getByRole("button", { name: new RegExp(label) }).click();
      await page.locator(".v3-composition-status").getByText("Live JEV", { exact: true }).waitFor();
    };
    const primary = () => page.locator('[data-slot="primary"]').getAttribute("data-component");
    try {
      await page.goto("http://127.0.0.1:3000/v3/");
      assert.equal(await page.locator("textarea").count(), 0);
      await page.getByRole("button", { name: "Start a transfer" }).click();
      await page.getByText("Live JEV", { exact: true }).waitFor();
      assert.equal(await primary(), "transfer_editor");
      const amount = page.getByLabel("Amount for task 1", { exact: true });
      await amount.fill("321.00");
      const before = requests.length;
      await page.screenshot({
        path: "../graphify-out/v3-transfer-desktop.png",
        fullPage: true,
      });
      await turn("Can I afford that");
      assert.equal(await amount.inputValue(), "321.00");
      assert.equal(await primary(), "balance_projection");
      await page.getByText("SGD 2,879.00", { exact: true }).waitFor();
      assert.equal(requests.length, before + 1);
      await page.screenshot({
        path: "../graphify-out/v3-affordability-desktop.png",
        fullPage: true,
      });
      await turn("What if I send");
      assert.equal(await primary(), "amount_simulator");
      await page.getByText("SGD 2,500.00", { exact: true }).waitFor();
      assert.equal(await amount.inputValue(), "321.00");
      const scenario = page.getByLabel("Scenario amount for task 1", {
        exact: true,
      });
      await scenario.fill("900.00");
      await page.getByText("SGD 2,300.00", { exact: true }).waitFor();
      assert.equal(await amount.inputValue(), "321.00");
      await page.getByRole("button", { name: "Review plan" }).click();
      await page.getByText("Live JEV", { exact: true }).waitFor();
      assert.equal(await primary(), "transaction_review");
      await page.getByText("SGD 321.00", { exact: true }).waitFor();
      assert.equal(await page.getByRole("button", { name: "Confirm demo", exact: true }).isDisabled(), true);
      await page.getByLabel("I reviewed task 1").check();
      await page.getByRole("button", { name: "Confirm demo", exact: true }).click();
      await page.getByText("Demo complete. No money moved.").waitFor();
      await turn("Make it ");
      assert.equal(await amount.inputValue(), "300.00");
      delayed = true;
      await page.getByRole("button", { name: /Can I afford that/ }).click();
      await amount.fill("333.00");
      if (release) release();
      delayed = false;
      await page.waitForTimeout(150);
      assert.equal(await amount.inputValue(), "333.00");
      assert.equal(await page.getByText("Shaping your workspace", { exact: true }).count(), 0);
      invalid = true;
      await page.getByRole("button", { name: /Can I afford that/ }).click();
      await page.getByRole("button", { name: "Retry JEV" }).waitFor();
      assert.equal(await amount.inputValue(), "333.00");
      invalid = false;
      fail = true;
      await page.getByRole("button", { name: "Retry JEV" }).click();
      await page.getByRole("button", { name: "Retry JEV" }).waitFor();
      await page.getByText("Developer inspector", { exact: true }).click();
      const inspector = await page.locator(".v3-inspector pre").innerText();
      for (const sensitive of ["John Doe", "333.00", "acc_1234", "pay_4521"])
        assert.ok(!inspector.includes(sensitive));
      assert.ok(!JSON.stringify(requests).includes("321.00"));
      assert.ok(!JSON.stringify(requests).includes("John"));
      assert.deepEqual(errors, []);
    } finally {
      if (release) release();
      await browser.close();
    }
  },
);
test(
  "V3 multi-task projections, explicit scenario adoption and mobile layouts",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1100 },
    });
    await page.route("**/api/presentation-v3", (r) =>
      r.fulfill({
        contentType: "application/json",
        body: JSON.stringify(answer(r.request().postDataJSON())),
      }),
    );
    try {
      await page.goto("http://127.0.0.1:3000/v3/");
      await page.getByRole("button", { name: "Pay & invest", exact: true }).click();
      await page.getByText("Live JEV", { exact: true }).waitFor();
      assert.equal(await page.locator('[data-component$="_editor"]').count(), 2);
      await page.getByLabel("Order type for task 2", { exact: true }).selectOption("limit");
      const limit = page.getByLabel("Limit price for task 2", { exact: true });
      await limit.waitFor();
      assert.equal(await page.getByRole("button", { name: "Review plan" }).isDisabled(), true);
      await limit.fill("225.00");
      await page.getByText("USD 22,500.00", { exact: true }).waitFor();
      await page.getByLabel("Order type for task 2", { exact: true }).selectOption("market");
      await page.getByRole("button", { name: /Can I afford both/ }).click();
      await page.getByText("SGD 1,960.00", { exact: true }).waitFor();
      await page.getByText("USD 7,250.00", { exact: true }).waitFor();
      await page.getByRole("button", { name: /What if I buy/ }).click();
      await page.getByText("USD 11,800.00", { exact: true }).waitFor();
      assert.equal(await page.getByLabel("Shares for task 2", { exact: true }).inputValue(), "100");
      await page.getByRole("button", { name: "Use these values" }).click();
      await page.getByText("Live JEV", { exact: true }).waitFor();
      assert.equal(await page.getByLabel("Shares for task 2", { exact: true }).inputValue(), "80");
      await page.screenshot({
        path: "../graphify-out/v3-multi-desktop.png",
        fullPage: true,
      });
      for (const width of [768, 390, 320]) {
        await page.setViewportSize({ width, height: 950 });
        await page.waitForTimeout(300);
        assert.ok(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          `overflow at ${width}`,
        );
      }
      await page.setViewportSize({ width: 390, height: 950 });
      await page.screenshot({
        path: "../graphify-out/v3-multi-mobile.png",
        fullPage: true,
      });
      await page.getByRole("button", { name: "Review plan" }).click();
      await page.getByLabel("I reviewed task 1").check();
      assert.equal(await page.getByRole("button", { name: "Confirm demo", exact: true }).isDisabled(), true);
      await page.getByLabel("I reviewed task 2").check();
      await page.getByRole("button", { name: "Confirm demo", exact: true }).click();
      await page.getByText("Demo complete. No money moved.").waitFor();
    } finally {
      await browser.close();
    }
  },
);

test(
  "V3 selection motion preserves input identity and respects reduced motion",
  { timeout: 60000 },
  async () => {
    const browser = await chromium.launch({ executablePath, headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    let primary = "balance_projection";
    const requests = [];
    await page.route("**/api/presentation-v3", (route) => {
      const ctx = route.request().postDataJSON();
      requests.push(ctx);
      const payload = answer(ctx);
      if (ctx.objective === "simulate") {
        const a = payload.answers.primary;
        a.choice = primary;
        a.probabilities = Object.fromEntries(
          Object.keys(a.probabilities).map((k) => [k, k === primary ? 1 : 0]),
        );
        payload.answers.continuity.choice = "recompose";
        payload.answers.continuity.probabilities = Object.fromEntries(
          Object.keys(payload.answers.continuity.probabilities).map((k) => [k, k === "recompose" ? 1 : 0]),
        );
      }
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(payload) });
    });
    try {
      await page.goto("http://127.0.0.1:3000/v3/");
      await page.getByRole("button", { name: /What if I send/ }).click();
      await page.getByText("Live JEV", { exact: true }).waitFor();
      const input = page.getByLabel("Amount for task 1", { exact: true });
      await input.fill("345.00");
      await input.evaluate((el) => {
        window.originalAmountInput = el;
      });
      await page.getByText("Developer inspector", { exact: true }).click();
      primary = "amount_simulator";
      await page.getByRole("button", { name: "Recompose", exact: true }).click();
      await page.getByText("Live JEV", { exact: true }).waitFor();
      assert.equal(await input.evaluate((el) => el === window.originalAmountInput), true);
      assert.equal(await input.inputValue(), "345.00");
      assert.equal(await page.locator('[data-slot="primary"]').getAttribute("data-component"), primary);
      await page.waitForTimeout(450);
      assert.equal(await page.locator(".v3-primary-marker").count(), 1);
      await page.screenshot({ path: "../graphify-out/v3-curated-scenario.png", fullPage: true });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.setViewportSize({ width: 390, height: 950 });
      await page.getByRole("button", { name: "Recompose", exact: true }).click();
      await page.getByText("Live JEV", { exact: true }).waitFor();
      assert.equal(requests.at(-1).viewport, "mobile");
      await page.waitForTimeout(350);
      const boxes = await page.locator(".v3-module").evaluateAll((nodes) =>
        nodes.map((n) => {
          const r = n.getBoundingClientRect();
          return { top: r.top, bottom: r.bottom };
        }),
      );
      for (let i = 1; i < boxes.length; i++)
        assert.ok(boxes[i].top >= boxes[i - 1].bottom - 1, "mobile panels overlap");
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: "../graphify-out/v3-curated-mobile.png", fullPage: true });
    } finally {
      await browser.close();
    }
  },
);
