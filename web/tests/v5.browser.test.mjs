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

async function setup(page, mode = "otp") {
  const calls = [];
  let draft, publicKey, payload;
  await page.route("**/api/presentation-v4/evaluate", (route) =>
    route.fulfill({ json: response(route.request().postDataJSON()) }),
  );
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
  await page.goto("http://127.0.0.1:3000/v5/");
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
