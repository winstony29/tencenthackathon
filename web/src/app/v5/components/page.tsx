"use client";
import Link from "next/link";
import { useState } from "react";
import { LayoutGroup } from "motion/react";
import { V5Enquiry, enquirySchema, type Enquiry } from "@/components/v5-enquiry";
import { request } from "@/lib/api";
import { V5Cards } from "@/components/v5-cards";
import { V5Orb } from "@/components/v5-orb";
import { demoHandoff } from "@/lib/v4-demo";
import { initialState, reducer } from "@/lib/v4-state";
import { contextFor } from "@/lib/v4-catalog";
import { fallback, accept } from "@/lib/v4-composition";
import "../../v4/v4.css";
import "../v5.css";
const examples = [
  "Transfer",
  "Transfer flow",
  "Bill payment",
  "Stock order",
  "Currency conversion",
  "Multiple tasks",
  "Balance projection",
  "Funding gap",
] as const;
function Sample({ name }: { name: (typeof examples)[number] }) {
  const [afford, setAfford] = useState(false);
  const multi = ["Bill payment", "Stock order", "Multiple tasks"].includes(name);
  const handoff = demoHandoff(
    multi ? "multi" : "transfer",
    name === "Balance projection" || afford ? 1 : 0,
    initialState,
  );
  if (name === "Bill payment") handoff.draft.actions = [handoff.draft.actions[0]];
  if (name === "Stock order") handoff.draft.actions = [handoff.draft.actions[1]];
  if (name === "Funding gap" && handoff.draft.actions[0].type === "transfer") {
    handoff.draft.actions[0].amount = "4000.00";
    handoff.objective = "assess_affordability";
  }
  if (name === "Currency conversion")
    handoff.draft.actions = [
      {
        type: "fx_conversion",
        from_account_id: "acc_1234",
        from_account_label: handoff.data.accounts.accounts.find((a) => a.account_id === "acc_1234")!.label,
        to_account_id: "acc_7788",
        to_account_label: handoff.data.accounts.accounts.find((a) => a.account_id === "acc_7788")!.label,
        sell_amount: "100.00",
        sell_currency: "SGD",
        buy_amount: "75.00",
        buy_currency: "USD",
        rate: "0.750000",
        rate_as_of: "2026-10-13T09:00:00+08:00",
      },
    ];
  const state = reducer(initialState, { type: "handoff", value: handoff });
  const composition = fallback(contextFor(state, "desktop"));
  if (name === "Transfer flow") {
    const editor = Object.values(composition.spec.elements).find((e) => e.type === "TaskEditor")!;
    editor.props.variant = "flow";
    composition.nodes = accept(composition.spec, contextFor(state, "desktop"));
  }
  return (
    <article>
      <h2 className="v5-gallery-label">{name}</h2>
      <div className="v5-gallery-card">
        <V5Cards
          state={state}
          composition={composition}
          preview
          onAfford={() => setAfford(true)}
          onCancel={() => setAfford(false)}
          onLock={() => {}}
        />
      </div>
    </article>
  );
}
export default function Components() {
  const [insights, setInsights] = useState<Enquiry | null>(null);
  const [error, setError] = useState("");
  async function loadInsights() {
    try {
      const data = await request("/account-insights?user_id=u_demo", enquirySchema.shape.data);
      const types = [
        "BalanceSummary",
        "AccountList",
        "UpcomingBills",
        "CashflowSummary",
        "TransactionList",
        "SpendingBreakdown",
        "PeriodComparison",
      ];
      setInsights(
        enquirySchema.parse({
          data,
          topic: "overview",
          source: "fallback",
          latency_ms: 0,
          spec: {
            root: "workspace",
            elements: {
              workspace: { type: "EnquiryWorkspace", variant: "split", children: types },
              ...Object.fromEntries(
                types.map((type) => [
                  type,
                  {
                    type,
                    variant:
                      type === "UpcomingBills" &&
                      data.forecasts.some((f) => !("unavailable" in f) && f.commitments.length)
                        ? "timeline"
                        : type === "CashflowSummary" && data.forecasts.some((f) => !("unavailable" in f))
                          ? "waterfall"
                          : "expanded",
                    children: [],
                  },
                ]),
              ),
            },
          },
        }),
      );
    } catch {
      setError("Account insights are unavailable. Try again.");
    }
  }
  const [active, setActive] = useState(false);
  return (
    <main className="v5-shell v5-gallery">
      <header>
        <Link href="/v5/">Back to app</Link>
        <h1>Component library</h1>
        <p>Current V5 components ? sample data ? payments disabled</p>
        <Link href="/v4/components/">Explore the earlier V4 library</Link>
      </header>
      <section aria-label="Read-only components">
        <h2>Account insights</h2>
        <p>Seven read-only blocks using the virtual bank&apos;s synthetic data.</p>
        <button className="v5-secondary" onClick={() => void loadInsights()}>
          Show account components
        </button>
        {error && <p role="alert">{error}</p>}
        {insights && (
          <div className="v5-gallery-card">
            <V5Enquiry value={insights} />
          </div>
        )}
      </section>
      <section className="v5-gallery-orbs" aria-label="Orb states">
        <div>
          <h2 className="v5-gallery-label">Request orb</h2>
          <LayoutGroup id="gallery-idle">
            <V5Orb active={active} onClick={() => setActive(!active)} />
          </LayoutGroup>
          <p id="v5-requests">{active ? "Choose a request in the app." : "Tap to activate"}</p>
        </div>
        <div>
          <h2 className="v5-gallery-label">Composing</h2>
          <LayoutGroup id="gallery-busy">
            <V5Orb busy />
          </LayoutGroup>
        </div>
      </section>
      <section className="v5-gallery-grid" aria-label="Task and analysis components">
        {examples.map((name) => (
          <Sample key={name} name={name} />
        ))}
      </section>
      <p>
        Account details expand inside each card. Payment results and OTP appear only during the bank flow.
      </p>
    </main>
  );
}
