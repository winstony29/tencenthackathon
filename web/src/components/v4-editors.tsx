"use client";
import {
  ArrowUpRight,
  ArrowLeft,
  ArrowLeftRight,
  Check,
  UserRound,
  ReceiptText,
  TrendingUp,
} from "lucide-react";
import { effective, type State, type Event } from "@/lib/v4-state";
import { displayMoney, estimatedTotal } from "@/lib/money";
import { Amount, Select, Heading, Notice } from "./v4-primitives";

export type ComponentProps = {
  state: State;
  dispatch: (event: Event) => void;
  action?: number | null;
  variant?: string;
};
export function TaskEditor({ state, dispatch, action: index = 0 }: ComponentProps) {
  if (index === null || !state.handoff) return null;
  const result = effective(state),
    a = result.actions[index];
  if (!a) return null;
  if (a.type === "fx_conversion")
    return (
      <>
        <Heading
          eyebrow={`TASK ${String(index + 1).padStart(2, "0")}`}
          title="Convert currency"
          aside={<ArrowLeftRight size={22} aria-hidden="true" />}
        />
        <div className="v4-payee">
          <span className="v4-avatar">
            <ArrowLeftRight size={21} />
          </span>
          <div>
            <strong>
              {displayMoney(a.sell_amount, a.sell_currency)} → {displayMoney(a.buy_amount, a.buy_currency)}
            </strong>
            <span>
              1 {a.sell_currency} = {a.rate} {a.buy_currency} · {a.from_account_label} to {a.to_account_label}
            </span>
          </div>
        </div>
        <Notice>Worked out from your request. Change the request to change it.</Notice>
      </>
    );
  const data = state.handoff.data.accounts,
    edits = state.edits[index] ?? {},
    stock = a.type === "stock_order";
  const Icon = stock
    ? TrendingUp
    : a.type === "transfer"
      ? UserRound
      : a.type === "account_transfer"
        ? ArrowLeftRight
        : ReceiptText;
  const edit = (field: string, value: string) => dispatch({ type: "edit", index, field, value });
  const account = data.accounts.find((b) => b.account_id === a.from_account_id);
  const orderType = edits.order_type ?? (stock ? a.order_type : "market");
  return (
    <>
      <Heading
        eyebrow={`TASK ${String(index + 1).padStart(2, "0")}`}
        title={
          stock
            ? `${a.side === "buy" ? "Buy" : "Sell"} ${a.ticker}`
            : a.type === "transfer"
              ? "Send money"
              : a.type === "account_transfer"
                ? "Move money"
                : "Pay your bill"
        }
        aside={<Icon size={22} aria-hidden="true" />}
      />
      {a.type === "transfer" ? (
        <Select
          label={`To for task ${index + 1}`}
          value={a.to_payee_id}
          onChange={(v) => edit("to_payee_id", v)}
        >
          {data.payees.map((p) => (
            <option key={p.payee_id} value={p.payee_id}>
              {p.label}
            </option>
          ))}
        </Select>
      ) : (
        <div className="v4-payee">
          <span className="v4-avatar">
            <Icon size={21} />
          </span>
          <div>
            <strong>
              {a.type === "stock_order"
                ? a.company_name
                : a.type === "account_transfer"
                  ? `To ${a.to_account_label}`
                  : a.biller_label}
            </strong>
            <span>
              {a.type === "stock_order"
                ? `${a.ticker} · ${a.currency}`
                : a.type === "account_transfer"
                  ? "Between your own accounts"
                  : `Reference ${a.bill_reference}`}
            </span>
          </div>
        </div>
      )}
      <Amount
        label={`${stock ? "Shares" : "Amount"} for task ${index + 1}`}
        value={stock ? (edits.quantity ?? String(a.quantity)) : (edits.amount ?? a.amount)}
        currency={a.currency}
        quantity={stock}
        onChange={(v) => edit(stock ? "quantity" : "amount", v)}
        error={result.errors[index]}
      />
      <div className="v4-fields">
        {a.type !== "account_transfer" && (
          <Select
            label={`From account for task ${index + 1}`}
            value={a.from_account_id}
            onChange={(v) => edit("from_account_id", v)}
          >
            {data.accounts
              .filter((b) => b.currency === a.currency)
              .map((b) => (
                <option key={b.account_id} value={b.account_id}>
                  {b.label}
                </option>
              ))}
          </Select>
        )}
        {stock && (
          <Select
            label={`Order type for task ${index + 1}`}
            value={orderType}
            onChange={(v) => edit("order_type", v)}
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </Select>
        )}
      </div>
      {stock && orderType === "limit" && (
        <Amount
          label={`Limit price for task ${index + 1}`}
          value={edits.limit_price ?? a.limit_price ?? ""}
          currency={a.currency}
          onChange={(v) => edit("limit_price", v)}
        />
      )}
      <div className="v4-funding">
        <span>Available</span>
        <strong>{account ? displayMoney(account.balance, a.currency) : "Unavailable"}</strong>
      </div>
      {a.type === "transfer" && <Notice>Listed recipient · bank verification pending</Notice>}
      {stock && <Notice>Estimated price. Fees and taxes excluded.</Notice>}
    </>
  );
}
export function TransactionReview({ state, dispatch }: ComponentProps) {
  const { actions, errors } = effective(state);
  return (
    <>
      <Heading eyebrow="YOUR DRAFT" title="One final check" aside={<span className="v4-badge">Demo</span>} />
      {actions.map((a, i) => {
        const stock = a.type === "stock_order";
        const quote = stock ? state.handoff?.data.quotes[a.ticker] : null;
        const price = stock
          ? a.order_type === "limit"
            ? a.limit_price
            : quote?.currency === a.currency
              ? quote.price
              : null
          : null;
        if (a.type === "fx_conversion")
          return (
            <section className="v4-review-task" key={i}>
              <h3>
                <span>{String(i + 1).padStart(2, "0")}</span>
                Convert {a.sell_currency} to {a.buy_currency}
              </h3>
              <dl>
                <div>
                  <dt>Sell</dt>
                  <dd>{displayMoney(a.sell_amount, a.sell_currency)}</dd>
                </div>
                <div>
                  <dt>Rate</dt>
                  <dd>
                    1 {a.sell_currency} = {a.rate} {a.buy_currency}
                  </dd>
                </div>
                <div>
                  <dt>Get</dt>
                  <dd>{displayMoney(a.buy_amount, a.buy_currency)}</dd>
                </div>
                <div>
                  <dt>Into</dt>
                  <dd>{a.to_account_label}</dd>
                </div>
              </dl>
              <label className="v4-ack">
                <input
                  type="checkbox"
                  checked={!!state.approved[i]}
                  onChange={() => dispatch({ type: "approve", index: i })}
                />
                I reviewed task {i + 1}
              </label>
            </section>
          );
        return (
          <section className="v4-review-task" key={i}>
            <h3>
              <span>{String(i + 1).padStart(2, "0")}</span>
              {stock
                ? `${a.side === "buy" ? "Buy" : "Sell"} ${a.ticker}`
                : a.type === "transfer"
                  ? a.to_payee_label
                  : a.type === "account_transfer"
                    ? `Move to ${a.to_account_label}`
                    : a.biller_label}
            </h3>
            <dl>
              <div>
                <dt>{stock ? "Shares" : "Amount"}</dt>
                <dd>{stock ? `${a.quantity} · ${a.currency}` : displayMoney(a.amount, a.currency)}</dd>
              </div>
              <div>
                <dt>{stock && a.side === "sell" ? "Into" : "From"}</dt>
                <dd>{a.from_account_label}</dd>
              </div>
              {stock && (
                <>
                  <div>
                    <dt>Order</dt>
                    <dd>
                      {a.order_type === "limit"
                        ? `Limit ${displayMoney(a.limit_price!, a.currency)}`
                        : "Market"}
                    </dd>
                  </div>
                  <div>
                    <dt>Estimated total</dt>
                    <dd>
                      {price ? displayMoney(estimatedTotal(price, a.quantity), a.currency) : "Unavailable"}
                    </dd>
                  </div>
                </>
              )}
              {a.type === "bill_payment" && (
                <div>
                  <dt>Reference</dt>
                  <dd>{a.bill_reference}</dd>
                </div>
              )}
            </dl>
            {a.type === "transfer" && <Notice>Listed recipient · bank verification pending</Notice>}
            <label className="v4-ack">
              <input
                type="checkbox"
                checked={!!state.approved[i]}
                onChange={() => dispatch({ type: "approve", index: i })}
              />
              I reviewed task {i + 1}
            </label>
          </section>
        );
      })}
      <Notice>Demo only. No money moves. Estimates exclude fees and taxes.</Notice>
      <div className="v4-review-actions">
        <button className="v4-text-button" onClick={() => dispatch({ type: "objective", value: "prepare" })}>
          <ArrowLeft size={16} /> Edit details
        </button>
        <button
          className="v4-button"
          disabled={!!Object.keys(errors).length || !actions.every((_, i) => state.approved[i])}
          onClick={() => dispatch({ type: "finish" })}
        >
          Confirm demo <Check size={16} />
        </button>
      </div>
    </>
  );
}
export function ReviewBar({ state, dispatch }: ComponentProps) {
  const valid = !Object.keys(effective(state).errors).length;
  return (
    <footer className="v4-review-bar">
      <p>
        {Object.keys(state.scenario).length ? "Review uses your actual draft." : "Your approval comes next."}
      </p>
      <button
        className="v4-button"
        disabled={!valid}
        onClick={() => dispatch({ type: "objective", value: "review" })}
      >
        Review{" "}
        {state.handoff?.draft.actions.length === 1
          ? state.handoff.draft.actions[0].type === "transfer"
            ? "transfer"
            : "task"
          : "plan"}{" "}
        <ArrowUpRight size={17} />
      </button>
    </footer>
  );
}
