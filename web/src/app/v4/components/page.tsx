"use client";
import Link from "next/link";
import { useReducer, type ReactNode } from "react";
import { TaskEditor, TransactionReview } from "@/components/v4-editors";
import {
  BalanceProjection,
  AmountSimulator,
  RecipientDetails,
  StockEstimate,
  FundingOptions,
} from "@/components/v4-analysis";
import { AccountInsights, FinancialStatus } from "@/components/v4-context";
import { V4Workspace } from "@/components/v4-workspace";
import { fallback } from "@/lib/v4-composition";
import { contextFor } from "@/lib/v4-catalog";
import { demoHandoff } from "@/lib/v4-demo";
import { initialState, reducer } from "@/lib/v4-state";
import { financialRows } from "@/lib/v4-financial";
import "../v4.css";
function Tile({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div>
      <p className="v4-note">{name}</p>
      <section className="v4-panel" aria-label={name}>
        {children}
      </section>
    </div>
  );
}
export default function ComponentPreview() {
  const [state, dispatch] = useReducer(reducer, initialState, (s) => {
    const handoff = demoHandoff("transfer", 0, s);
    handoff.data.accounts.accounts.push({
      account_id: "acc_preview",
      label: "Everyday savings",
      type: "savings",
      currency: "SGD",
      balance: "1800.00",
    });
    return reducer(s, { type: "handoff", value: handoff });
  });
  const [multi, multiDispatch] = useReducer(reducer, initialState, (s) =>
    reducer(s, { type: "handoff", value: demoHandoff("multi", 0, s) }),
  );
  const [simulation, scenarioDispatch] = useReducer(reducer, initialState, (s) =>
    reducer(s, { type: "handoff", value: demoHandoff("transfer", 2, s) }),
  );
  const [review, reviewDispatch] = useReducer(reducer, initialState, (s) =>
    reducer(s, { type: "handoff", value: demoHandoff("multi", 3, s) }),
  );
  const shortfall = financialRows(state).some((a) => a.remaining !== null && a.remaining < 0n);
  return (
    <div className="v4-shell">
      <div className="v4-gallery">
        <header>
          <Link href="/v4/">Back to workspace</Link>
          <p className="v4-note">V4 / COMPONENT PREVIEW</p>
          <h1>Explore every V4 component.</h1>
          <p>
            Eight visible component types, all three task editors, and the composed workspace. Interactive
            mock data; no JEV calls.
          </p>
          <div className="v4-gallery-controls" aria-label="Example amounts">
            <button
              className="v4-secondary-button"
              onClick={() => dispatch({ type: "edit", index: 0, field: "amount", value: "500.00" })}
            >
              Normal transfer
            </button>
            <button
              className="v4-secondary-button"
              onClick={() => dispatch({ type: "edit", index: 0, field: "amount", value: "4000.00" })}
            >
              Show funding gap
            </button>
          </div>
        </header>
        <FinancialStatus state={state} />
        <div className="v4-gallery-grid">
          <Tile name="TaskEditor / transfer">
            <TaskEditor state={state} dispatch={dispatch} action={0} variant="full" />
          </Tile>
          <Tile name="BalanceProjection / summary or funding gap">
            <BalanceProjection
              state={state}
              dispatch={dispatch}
              variant={shortfall ? "funding-gap" : "summary"}
            />
          </Tile>
          <Tile name="AccountInsights / expanded">
            <AccountInsights state={state} dispatch={dispatch} variant="expanded" />
          </Tile>
          <Tile name="TaskEditor / bill payment">
            <TaskEditor state={multi} dispatch={multiDispatch} action={0} variant="full" />
          </Tile>
          <Tile name="TaskEditor / stock order">
            <TaskEditor state={multi} dispatch={multiDispatch} action={1} variant="compact" />
          </Tile>
          <Tile name="StockEstimate">
            <StockEstimate state={multi} dispatch={multiDispatch} action={1} />
          </Tile>
          <Tile name="AmountSimulator">
            <AmountSimulator state={simulation} dispatch={scenarioDispatch} />
          </Tile>
          <Tile name="RecipientDetails">
            <RecipientDetails state={state} dispatch={dispatch} action={0} />
          </Tile>
          <Tile name="FundingOptions">
            <FundingOptions state={state} dispatch={dispatch} />
          </Tile>
          <Tile name="TransactionReview">
            <TransactionReview state={review} dispatch={reviewDispatch} />
          </Tile>
        </div>
        <details style={{ marginTop: 32 }}>
          <summary className="v4-secondary-button">Workspace + TaskGroup + DecisionGroup</summary>
          <p className="v4-note">
            Structural components arrange the visible controls. This example pairs a simulator with its
            projection and groups the task editor.
          </p>
          <V4Workspace
            composition={fallback(contextFor(simulation, "desktop"))}
            state={simulation}
            dispatch={scenarioDispatch}
          />
        </details>
      </div>
    </div>
  );
}
