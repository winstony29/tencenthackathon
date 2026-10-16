"use client";
import { ArrowRight, SlidersHorizontal } from "lucide-react";
import { effective } from "@/lib/v4-state";
import { projectedBalances } from "@/lib/draft-editor";
import { displayMoney, estimatedTotal, fromCurrency } from "@/lib/money";
import { Amount, Heading, Metric, Notice } from "./v4-primitives";
import type { ComponentProps } from "./v4-editors";

export function BalanceProjection({ state, variant }: ComponentProps) {
  if (!state.handoff) return null;
  const values = effective(state, true),
    hypothetical = Object.keys(state.scenario).length > 0;
  const rows = Object.keys(values.errors).length
    ? []
    : projectedBalances(values.actions, state.handoff.data.accounts, state.handoff.data.quotes);
  return (
    <>
      <Heading
        eyebrow={hypothetical ? "WHAT IF" : undefined}
        title="Balance after"
        aside={<span className="v4-badge">Estimate</span>}
      />
      {!rows.length && <Notice error>Check the amounts to see your projection.</Notice>}
      <div className="v4-balances">
        {rows.map((a) => {
          const cents = BigInt(a.balance.replace(".", "")),
            remaining = a.projected === null ? null : BigInt(a.projected.replace(".", ""));
          const percent =
            remaining === null || cents <= 0n
              ? 0
              : Math.max(0, Math.min(100, Number((remaining * 100n) / cents)));
          return (
            <div key={a.account_id} className="v4-balance">
              <Metric label={a.label} value={a.projected} currency={a.currency} />
              {a.projected?.startsWith("-") && (
                <p className="v4-error">Shortfall: {displayMoney(a.projected.slice(1), a.currency)}</p>
              )}
              {variant === "funding-gap" && remaining !== null && remaining < 0n && (
                <svg
                  viewBox="0 0 300 58"
                  role="img"
                  aria-label={`Funding gap: ${displayMoney(a.projected!.slice(1), a.currency)}`}
                  style={{ width: "100%", maxWidth: 400 }}
                >
                  <rect x="0" y="8" width="300" height="12" rx="6" fill="#f2dadb" />
                  <rect
                    x="0"
                    y="8"
                    width={Number((cents * 300n) / (cents - remaining))}
                    height="12"
                    rx="6"
                    fill="#1a5b48"
                  />
                  <text x="0" y="46" fontSize="12" fill="#202326">
                    Current balance
                  </text>
                  <text x="300" y="46" textAnchor="end" fontSize="12" fill="#9f1726">
                    Funding gap
                  </text>
                </svg>
              )}
              {variant === "summary" ? (
                <>
                  <div className="v4-balance-track" aria-hidden="true">
                    <span style={{ width: `${percent}%` }} />
                  </div>
                  <div className="v4-funding">
                    <span>Available now</span>
                    <strong>{displayMoney(a.balance, a.currency)}</strong>
                  </div>
                </>
              ) : (
                <dl className="v4-breakdown">
                  <div>
                    <dt>Available now</dt>
                    <dd>{displayMoney(a.balance, a.currency)}</dd>
                  </div>
                  <div>
                    <dt>Change</dt>
                    <dd>{a.projected === null ? "Unavailable" : displayMoney(a.change, a.currency)}</dd>
                  </div>
                </dl>
              )}
            </div>
          );
        })}
      </div>
      <Notice>
        Excludes fees and upcoming bills.
      </Notice>
    </>
  );
}
export function AmountSimulator({ state, dispatch }: ComponentProps) {
  const values = effective(state, true),
    actual = effective(state);
  return (
    <>
      <Heading
        eyebrow="WHAT IF"
        title="Adjust amount"
        aside={<SlidersHorizontal size={21} aria-hidden="true" />}
      />
      {values.actions.map((a, i) => {
        if (a.type === "fx_conversion") return null; // derived from the other actions; no what-if
        const stock = a.type === "stock_order",
          field = stock ? "quantity" : "amount";
        const raw = state.scenario[i]?.[field] ?? (stock ? String(a.quantity) : a.amount);
        const valid = stock
          ? /^\d+$/.test(raw) && Number.isSafeInteger(Number(raw))
          : /^\d+\.\d{2}$/.test(raw);
        const units = valid ? BigInt(raw.replace(".", "")) : 0n;
        const account = state.handoff!.data.accounts.accounts.find((b) => b.account_id === a.from_account_id);
        const max = stock ? 200n : BigInt((account?.balance ?? "0.00").replace(".", ""));
        const committed = actual.actions[i];
        const edit = (value: string) => dispatch({ type: "scenario", index: i, field, value });
        return (
          <div className="v4-scenario-control" key={i}>
            <Amount
              label={`${stock ? "Shares" : "Amount"} for task ${i + 1}`}
              value={raw}
              currency={a.currency}
              quantity={stock}
              onChange={edit}
              error={values.errors[i]}
            />
            {valid && units <= 10000000n && max > 0n && max <= 10000000n && (
              <input
                className="v4-range"
                aria-label={`Adjust scenario for task ${i + 1}`}
                type="range"
                min={1}
                max={Number(units > max ? units : max)}
                step={1}
                value={Number(units)}
                onChange={(e) => {
                  const n = BigInt(e.target.value);
                  edit(stock ? String(n) : `${n / 100n}.${String(n % 100n).padStart(2, "0")}`);
                }}
              />
            )}
            <p className="v4-note">
              Current:{" "}
              {committed.type === "stock_order"
                ? `${committed.quantity} ${committed.ticker} shares`
                : committed.type === "fx_conversion"
                  ? displayMoney(committed.sell_amount, committed.sell_currency)
                  : displayMoney(committed.amount, committed.currency)}
            </p>
          </div>
        );
      })}
      <button
        className="v4-secondary-button"
        disabled={!Object.keys(state.scenario).length || !!Object.keys(values.errors).length}
        onClick={() => dispatch({ type: "apply" })}
      >
        Apply changes <ArrowRight size={16} />
      </button>
    </>
  );
}
export function StockEstimate({ state, action: index }: ComponentProps) {
  if (index === null || index === undefined) return null;
  const values = effective(state, true),
    a = values.actions[index];
  if (a?.type !== "stock_order") return null;
  const quote = state.handoff?.data.quotes[a.ticker];
  const price =
    a.order_type === "limit" ? a.limit_price : quote?.currency === a.currency ? quote.price : null;
  return (
    <>
      <Heading eyebrow="ORDER ESTIMATE" title={`${a.quantity} ${a.ticker} shares`} />
      <Metric
        label={a.order_type === "limit" ? "Limit total" : "Estimated total"}
        value={price && !values.errors[index] ? estimatedTotal(price, a.quantity) : null}
        currency={a.currency}
      />
      <Notice>
        {price ? `${displayMoney(price, a.currency)} / share. ` : "Price unavailable. "}
        Fees excluded. Price may change.
      </Notice>
    </>
  );
}
export function RecipientDetails({ state, action: index }: ComponentProps) {
  const a = index === null || index === undefined ? null : effective(state).actions[index];
  return a?.type === "transfer" ? (
    <>
      <Heading eyebrow="RECIPIENT" title={a.to_payee_label} />
    </>
  ) : null;
}
export function FundingOptions({ state }: ComponentProps) {
  if (!state.handoff) return null;
  const currencies = new Set(effective(state).actions.map(fromCurrency));
  const accounts = state.handoff.data.accounts.accounts;
  return (
    <>
      <Heading title="Available accounts" />
      {accounts
        .filter(
          (a) => currencies.has(a.currency) && accounts.filter((b) => b.currency === a.currency).length > 1,
        )
        .map((a) => (
          <Metric key={a.account_id} label={a.label} value={a.balance} currency={a.currency} />
        ))}
    </>
  );
}
