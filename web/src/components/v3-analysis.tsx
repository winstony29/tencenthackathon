"use client";
import { ArrowRight, Check, SlidersHorizontal } from "lucide-react";
import { effective, scenarioActions } from "@/lib/v3-state";
import { projectedBalances } from "@/lib/draft-editor";
import { v2Accounts } from "@/lib/v2-parser";
import { exampleQuote } from "@/lib/examples";
import { displayMoney, estimatedTotal } from "@/lib/money";
import { AmountInput, FinancialMetric, SectionHeading, InlineNotice } from "./v3-primitives";
import { Button } from "./ui/button";
import type { ComponentProps } from "./v3-editors";

function projection(state: ComponentProps["state"]) {
  const values = scenarioActions(state);
  return Object.keys(values.errors).length
    ? []
    : projectedBalances(values.actions, v2Accounts, { AAPL: exampleQuote });
}
export function BalanceProjection({ state }: ComponentProps) {
  const rows = projection(state),
    scenario = Object.keys(state.scenario).length > 0;
  return (
    <>
      <SectionHeading
        eyebrow={scenario ? "WHAT IF" : "AFTER YOUR PLAN"}
        title="Your remaining balance"
        aside={<span className="v3-tag">Estimate</span>}
      />
      {!rows.length && <InlineNotice error>Check amounts to see your projection.</InlineNotice>}
      <div className="v3-projections">
        {rows.map((a) => (
          <div key={a.account_id}>
            <FinancialMetric
              label={a.label}
              value={a.projected}
              currency={a.currency}
              note={
                a.projected?.startsWith("-")
                  ? "Insufficient funds"
                  : scenario
                    ? "Hypothetical balance"
                    : "Estimated balance"
              }
            />
            <div className="v3-balance-track" aria-hidden="true">
              <span
                style={{
                  width: a.projected
                    ? `${Math.max(0, Math.min(100, Number((BigInt(a.projected.replace(".", "")) * 100n) / (BigInt(a.balance.replace(".", "")) || 1n))))}%`
                    : "0%",
                }}
              />
            </div>
            <div className="v3-track-label">
              <span>Available now</span>
              <b>{displayMoney(a.balance, a.currency)}</b>
            </div>
          </div>
        ))}
      </div>
      <InlineNotice>Excludes fees, taxes and other commitments. Currencies kept separate.</InlineNotice>
    </>
  );
}
export function CashFlow({ state }: ComponentProps) {
  return (
    <>
      <SectionHeading eyebrow="BREAKDOWN" title="The numbers" />
      {projection(state).map((a) => (
        <dl className="v3-breakdown" key={a.account_id}>
          <div>
            <dt>{a.label}</dt>
            <dd>{displayMoney(a.balance, a.currency)}</dd>
          </div>
          <div>
            <dt>Plan impact</dt>
            <dd>{displayMoney(a.change, a.currency)}</dd>
          </div>
          <div>
            <dt>Remaining</dt>
            <dd>{a.projected ? displayMoney(a.projected, a.currency) : "Unavailable"}</dd>
          </div>
        </dl>
      ))}
    </>
  );
}
export function AmountSimulator({ state, dispatch }: ComponentProps) {
  const current = effective(state),
    simulated = scenarioActions(state);
  const scenario = Object.keys(state.scenario).length > 0;
  return (
    <>
      <SectionHeading
        eyebrow="SCENARIO"
        title="Find your amount"
        aside={<SlidersHorizontal size={22} aria-hidden="true" />}
      />
      {simulated.actions.map((a, i) => {
        const stock = a.type === "stock_order",
          field = stock ? "quantity" : "amount";
        const raw = state.scenario[i]?.[field] ?? (stock ? String(a.quantity) : a.amount);
        const valid = stock
          ? /^\d+$/.test(raw) && Number.isSafeInteger(Number(raw))
          : /^\d+\.\d{2}$/.test(raw);
        const units = valid ? BigInt(raw.replace(".", "")) : 0n;
        const committed = current.actions[i];
        return (
          <div key={i} className="v3-scenario-control">
            <AmountInput
              label={`Scenario ${stock ? "shares" : "amount"} for task ${i + 1}`}
              value={raw}
              currency={a.currency}
              quantity={stock}
              onChange={(value) => dispatch({ type: "scenario", index: i, field, value })}
              error={simulated.errors[i]}
            />
            {valid && units <= 10000000n && (
              <input
                className="v3-range"
                type="range"
                aria-label={`Adjust scenario for task ${i + 1}`}
                min={stock ? 1 : 100}
                max={Math.max(stock ? 200 : 320000, Number(units))}
                step={stock ? 1 : 100}
                value={Number(units)}
                onChange={(e) => {
                  const n = BigInt(e.target.value);
                  dispatch({
                    type: "scenario",
                    index: i,
                    field,
                    value: stock ? String(n) : `${n / 100n}.${String(n % 100n).padStart(2, "0")}`,
                  });
                }}
              />
            )}
            <p className="v3-note">
              Draft:{" "}
              {committed.type === "stock_order"
                ? `${committed.quantity} shares`
                : displayMoney(committed.amount, committed.currency)}
            </p>
            {simulated.errors[i] && (
              <InlineNotice error>
                Enter a valid positive {stock ? "whole quantity" : "amount with two decimals"}.
              </InlineNotice>
            )}
          </div>
        );
      })}
      <div className="v3-scenario-footer">
        <span>Exploring only. Your draft stays as is.</span>
        <Button
          variant="outline"
          disabled={!scenario || !!Object.keys(simulated.errors).length}
          onClick={() => dispatch({ type: "apply" })}
        >
          Use these values <ArrowRight size={15} />
        </Button>
      </div>
    </>
  );
}
export function RecipientCheck({ state }: ComponentProps) {
  return (
    <>
      <SectionHeading eyebrow="RECIPIENT" title="The right person" />
      {effective(state)
        .actions.filter((a) => a.type === "transfer")
        .map((a, i) => (
          <div className="v3-recipient-line" key={i}>
            <span className="v3-person-icon">{a.to_payee_label.slice(0, 1)}</span>
            <strong>{a.to_payee_label}</strong>
          </div>
        ))}
      <InlineNotice>Listed demo recipient. Bank verification pending.</InlineNotice>
    </>
  );
}
export function AccountComparison({ state }: ComponentProps) {
  const currencies = new Set(effective(state).actions.map((a) => a.currency));
  return (
    <>
      <SectionHeading eyebrow="FUNDING" title="Your options" />
      {v2Accounts.accounts
        .filter(
          (a) =>
            currencies.has(a.currency) &&
            v2Accounts.accounts.filter((b) => b.currency === a.currency).length > 1,
        )
        .map((a) => (
          <FinancialMetric key={a.account_id} label={a.label} value={a.balance} currency={a.currency} />
        ))}
    </>
  );
}
export function InvestmentCost({ state }: ComponentProps) {
  const values = scenarioActions(state);
  return (
    <>
      <SectionHeading eyebrow="INVESTMENT" title="Order estimate" />
      {Object.keys(values.errors).length ? (
        <InlineNotice>Check task details.</InlineNotice>
      ) : (
        values.actions
          .filter((a) => a.type === "stock_order")
          .map((a, i) => {
            const price =
              a.order_type === "limit"
                ? a.limit_price
                : a.ticker === exampleQuote.ticker && a.currency === exampleQuote.currency
                  ? exampleQuote.price
                  : null;
            return (
              <div key={i}>
                <FinancialMetric
                  label={`${a.quantity} ${a.ticker} shares`}
                  value={price ? estimatedTotal(price, a.quantity) : null}
                  currency={a.currency}
                />
                <InlineNotice>
                  {price ? `${displayMoney(price, a.currency)} / share` : "Price unavailable"} ·{" "}
                  {a.order_type === "limit" ? "Limit" : "Mock quote"}
                </InlineNotice>
              </div>
            );
          })
      )}
      <InlineNotice>Fees and taxes excluded. Final price may vary.</InlineNotice>
    </>
  );
}
export function InvestmentRisk() {
  return (
    <>
      <SectionHeading eyebrow="CONTEXT" title="Before you invest" />
      {v2Accounts.holdings.map((h) => (
        <p className="v3-holding" key={h.ticker}>
          {h.ticker}
          <b>{h.quantity} shares held</b>
        </p>
      ))}
      <InlineNotice>Market prices can move. An estimate is not an execution price.</InlineNotice>
    </>
  );
}
export function MultiSummary({ state }: ComponentProps) {
  return (
    <>
      <SectionHeading eyebrow="TOGETHER" title="One plan, separate tasks" />
      {effective(state).actions.map((a, i) => (
        <div className="v3-summary-line" key={i}>
          <span>{String(i + 1).padStart(2, "0")}</span>
          <strong>
            {a.type === "stock_order"
              ? `${a.side} ${a.quantity} ${a.ticker}`
              : a.type === "transfer"
                ? a.to_payee_label
                : a.biller_label}
          </strong>
          <b>{a.type === "stock_order" ? a.currency : displayMoney(a.amount, a.currency)}</b>
        </div>
      ))}
      <InlineNotice>
        <Check size={13} aria-hidden="true" /> Review together. Currencies stay separate.
      </InlineNotice>
    </>
  );
}
