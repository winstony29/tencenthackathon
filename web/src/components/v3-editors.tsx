"use client";
import { ArrowUpRight, UserRound, ReceiptText, TrendingUp } from "lucide-react";
import { effective, type State, type Event } from "@/lib/v3-state";
import { v2Accounts } from "@/lib/v2-parser";
import { displayMoney } from "@/lib/money";
import { AmountInput, SelectField, SectionHeading, InlineNotice } from "./v3-primitives";
import { Button } from "./ui/button";

export type ComponentProps = { state: State; dispatch: (event: Event) => void; action?: number | null };
export function TaskEditor({ state, dispatch, action: index = 0 }: ComponentProps) {
  if (index === null) return null;
  const result = effective(state),
    action = result.actions[index];
  if (!action) return null;
  const edits = state.task.edits[index] ?? {};
  const stock = action.type === "stock_order";
  const orderType = edits.order_type ?? (stock ? action.order_type : "market");
  const edit = (field: string, value: string) => dispatch({ type: "edit", index, field, value });
  const name =
    action.type === "transfer"
      ? action.to_payee_label
      : action.type === "bill_payment"
        ? action.biller_label
        : action.company_name;
  const Icon = stock ? TrendingUp : action.type === "transfer" ? UserRound : ReceiptText;
  return (
    <>
      <SectionHeading
        eyebrow={`TASK ${String(index + 1).padStart(2, "0")}`}
        title={
          stock
            ? `${action.side === "buy" ? "Buy" : "Sell"} ${action.ticker}`
            : action.type === "transfer"
              ? "Send money"
              : "Pay your bill"
        }
        aside={<ArrowUpRight size={22} aria-hidden="true" />}
      />
      <div className="v3-person">
        <span className="v3-person-icon">
          <Icon size={22} aria-hidden="true" />
        </span>
        <div>
          <small>{stock ? "Investment" : "To"}</small>
          <strong>{name}</strong>
        </div>
        <span className="v3-tag">{stock ? action.currency : "Listed"}</span>
      </div>
      <AmountInput
        label={stock ? `Shares for task ${index + 1}` : `Amount for task ${index + 1}`}
        value={stock ? (edits.quantity ?? String(action.quantity)) : (edits.amount ?? action.amount)}
        currency={action.currency}
        quantity={stock}
        error={result.errors[index]}
        onChange={(v) => edit(stock ? "quantity" : "amount", v)}
      />
      <div className="v3-field-grid">
        <SelectField
          label={`From account for task ${index + 1}`}
          value={action.from_account_id}
          onChange={(v) => edit("from_account_id", v)}
        >
          {v2Accounts.accounts
            .filter((a) => a.currency === action.currency)
            .map((a) => (
              <option key={a.account_id} value={a.account_id}>
                {a.label}
              </option>
            ))}
        </SelectField>
        {action.type === "transfer" && (
          <SelectField
            label={`Recipient for task ${index + 1}`}
            value={action.to_payee_id}
            onChange={(v) => edit("to_payee_id", v)}
          >
            {v2Accounts.payees.map((p) => (
              <option key={p.payee_id} value={p.payee_id}>
                {p.label}
              </option>
            ))}
          </SelectField>
        )}
        {stock && (
          <SelectField
            label={`Order type for task ${index + 1}`}
            value={orderType}
            onChange={(v) => edit("order_type", v)}
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </SelectField>
        )}
      </div>
      {stock && orderType === "limit" && (
        <AmountInput
          label={`Limit price for task ${index + 1}`}
          value={edits.limit_price ?? action.limit_price ?? ""}
          currency={action.currency}
          onChange={(v) => edit("limit_price", v)}
        />
      )}
      {action.type === "bill_payment" && <InlineNotice>Reference {action.bill_reference}</InlineNotice>}
      <div className="v3-funding">
        <span>Available in this account</span>
        <b>
          {displayMoney(
            v2Accounts.accounts.find((a) => a.account_id === action.from_account_id)!.balance,
            action.currency,
          )}
        </b>
      </div>
      {result.errors[index] && <InlineNotice error>{result.errors[index]}</InlineNotice>}
    </>
  );
}
export function TransactionReview({ state, dispatch }: ComponentProps) {
  const { actions } = effective(state);
  return (
    <>
      <SectionHeading eyebrow="REVIEW" title="One final check" aside={<span className="v3-tag">Demo</span>} />
      {actions.map((a, i) => (
        <section className="v3-review-task" key={i}>
          <h3>
            {a.type === "transfer"
              ? a.to_payee_label
              : a.type === "bill_payment"
                ? a.biller_label
                : `${a.side === "buy" ? "Buy" : "Sell"} ${a.ticker}`}
          </h3>
          <dl>
            <div>
              <dt>{a.type === "stock_order" ? "Shares" : "Amount"}</dt>
              <dd>
                {a.type === "stock_order"
                  ? `${a.quantity} · ${a.currency}`
                  : displayMoney(a.amount, a.currency)}
              </dd>
            </div>
            <div>
              <dt>{a.type === "stock_order" && a.side === "sell" ? "Into" : "From"}</dt>
              <dd>{a.from_account_label}</dd>
            </div>
            {a.type === "bill_payment" && (
              <div>
                <dt>Reference</dt>
                <dd>{a.bill_reference}</dd>
              </div>
            )}
            {a.type === "stock_order" && (
              <div>
                <dt>Order</dt>
                <dd>
                  {a.order_type === "limit" ? `Limit ${displayMoney(a.limit_price!, a.currency)}` : "Market"}
                </dd>
              </div>
            )}
          </dl>
          {a.type === "transfer" && <InlineNotice>Listed recipient. Bank verification pending.</InlineNotice>}
          <label className="v3-approval">
            <input
              type="checkbox"
              checked={!!state.task.approved[i]}
              onChange={() => dispatch({ type: "approve", index: i })}
            />
            I reviewed task {i + 1}
          </label>
        </section>
      ))}
      <InlineNotice>Fees unavailable. Demo only — no money will move.</InlineNotice>
      <div className="v3-review-actions">
        <Button variant="ghost" onClick={() => dispatch({ type: "back" })}>
          Edit details
        </Button>
        <Button
          disabled={!actions.every((_, i) => state.task.approved[i])}
          onClick={() => dispatch({ type: "finish" })}
        >
          Confirm demo <ArrowUpRight size={16} />
        </Button>
      </div>
    </>
  );
}
