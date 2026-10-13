"use client";

import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { ArrowLeftRight, TrendingUp, ReceiptText } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Button } from "@/components/ui/button";
import type { DraftEdits } from "@/lib/draft-editor";
import type { Accounts, Action, Quote } from "@/lib/contracts";
import { displayMoney, estimatedTotal, fromCurrency } from "@/lib/money";

export function ActionCard({
  action,
  index,
  quote,
  editor,
}: {
  action: Action;
  index: number;
  quote?: Quote;
  editor?: {
    edits: DraftEdits;
    accounts: Accounts | null;
    error?: string;
    approved: boolean;
    disabled: boolean;
    showApproval?: boolean;
    onEdit: (index: number, field: string, value: string) => void;
    onApprove: (index: number) => void;
  };
}) {
  const reducedMotion = useReducedMotion();
  const quoteLabel = quote ? `${quote.ticker}:${quote.currency}:${quote.price}` : "waiting";
  const reveal = { hidden: { opacity: 0, y: reducedMotion ? 0 : 18 }, visible: { opacity: 1, y: 0 } };
  const title =
    action.type === "transfer"
      ? "Send money"
      : action.type === "bill_payment"
        ? "Pay your bill"
        : action.type === "fx_conversion"
          ? "Convert currency"
          : action.type === "account_transfer"
            ? "Move money"
            : `${action.side === "buy" ? "Buy" : "Sell"} shares`;
  const recipient =
    action.type === "transfer"
      ? action.to_payee_label
      : action.type === "bill_payment"
        ? action.biller_label
        : action.type === "fx_conversion" || action.type === "account_transfer"
          ? action.to_account_label
          : action.company_name;
  const usableQuote =
    action.type === "stock_order" && quote?.ticker === action.ticker && quote.currency === action.currency
      ? quote
      : undefined;
  const estimatePrice =
    action.type === "stock_order" && action.order_type === "limit" ? action.limit_price : usableQuote?.price;
  return (
    <motion.article
      className="animated-task"
      aria-labelledby={`task-${index}`}
      layout={reducedMotion ? false : "position"}
      variants={reveal}
      initial="hidden"
      animate="visible"
      transition={{ duration: reducedMotion ? 0 : 0.35, delay: reducedMotion ? 0 : index * 0.09 }}
      whileHover={reducedMotion ? undefined : { y: -3 }}
    >
      <Card className="task-card">
        <CardHeader>
          <div className="card-top">
            <span className="eyebrow">ACTION {String(index + 1).padStart(2, "0")}</span>
            <span className={`badge ${editor?.approved ? "approved" : ""}`}>
              {editor?.approved ? "Reviewed" : "Review required"}
            </span>
          </div>
          <CardTitle id={`task-${index}`}>
            <motion.span
              className="card-title-reveal"
              initial={reducedMotion ? false : { opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: reducedMotion ? 0 : index * 0.09 + 0.12 }}
            >
              {title}
            </motion.span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="recipient">
            <span className="recipient-avatar" aria-hidden="true">
              {action.type === "stock_order" ? (
                <TrendingUp size={20} aria-hidden="true" />
              ) : action.type === "bill_payment" ? (
                <ReceiptText size={20} aria-hidden="true" />
              ) : action.type === "fx_conversion" || action.type === "account_transfer" ? (
                <ArrowLeftRight size={20} aria-hidden="true" />
              ) : (
                recipient.charAt(0)
              )}
            </span>
            <div>
              <span className="field-label">
                {action.type === "stock_order"
                  ? action.ticker
                  : action.type === "transfer" || action.type === "account_transfer"
                    ? "To"
                    : action.type === "fx_conversion"
                      ? "Into"
                      : "Biller"}
              </span>
              <strong>{recipient}</strong>
            </div>
          </div>
          {editor && action.type !== "fx_conversion" ? (
            <ActionFields action={action} index={index} editor={editor} />
          ) : (
            <div className="amount-block">
              <span className="field-label">
                {action.type === "stock_order"
                  ? "Quantity"
                  : action.type === "fx_conversion"
                    ? "Sell"
                    : "Amount"}
              </span>
              <p className="amount">
                {action.type === "stock_order"
                  ? `${action.quantity} shares`
                  : action.type === "fx_conversion"
                    ? displayMoney(action.sell_amount, action.sell_currency)
                    : displayMoney(action.amount, action.currency)}
              </p>
            </div>
          )}
          <dl className="detail-list">
            {action.type === "fx_conversion" && (
              <>
                <div>
                  <dt>Rate</dt>
                  <dd>
                    1 {action.sell_currency} = {action.rate} {action.buy_currency} ·{" "}
                    {new Date(action.rate_as_of).toLocaleString()}
                  </dd>
                </div>
                <div>
                  <dt>Get</dt>
                  <dd>{displayMoney(action.buy_amount, action.buy_currency)}</dd>
                </div>
              </>
            )}
            {action.type === "bill_payment" && (
              <div>
                <dt>Bill reference</dt>
                <dd>{action.bill_reference}</dd>
              </div>
            )}
            {action.type === "stock_order" && (
              <>
                <div>
                  <dt>Order type</dt>
                  <dd>
                    {action.order_type === "market"
                      ? "Market order"
                      : `Limit · ${displayMoney(action.limit_price!, action.currency)}`}
                  </dd>
                </div>
                <div>
                  <dt>Price now · estimate</dt>
                  <dd>
                    <AnimatePresence initial={false} mode="wait">
                      <motion.span
                        className="quote-value"
                        key={quoteLabel}
                        initial={{ opacity: reducedMotion ? 1 : 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: reducedMotion ? 1 : 0 }}
                        transition={{ duration: reducedMotion ? 0 : 0.12 }}
                      >
                        {usableQuote
                          ? displayMoney(usableQuote.price, usableQuote.currency)
                          : "Waiting for quote"}
                      </motion.span>
                    </AnimatePresence>
                  </dd>
                </div>
                <div>
                  <dt>Estimated total</dt>
                  <dd>
                    {estimatePrice
                      ? displayMoney(estimatedTotal(estimatePrice, action.quantity), action.currency)
                      : "—"}
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt>
                {action.type === "stock_order" && action.side === "sell"
                  ? "Pay into"
                  : action.type === "fx_conversion"
                    ? "From"
                    : "Pay from"}
              </dt>
              <dd>{action.from_account_label}</dd>
            </div>
          </dl>
          {action.type === "stock_order" && (
            <p className="fine-print">
              Mock price. Estimates exclude fees and taxes; execution prices may differ.
            </p>
          )}
          {editor && editor.showApproval !== false && (
            <Button
              variant={editor.approved ? "ghost" : "outline"}
              disabled={editor.disabled || !!editor.error}
              aria-pressed={editor.approved}
              onClick={() => editor.onApprove(index)}
            >
              {editor.approved ? `Action ${index + 1} reviewed` : `Approve action ${index + 1}`}
            </Button>
          )}
        </CardContent>
      </Card>
    </motion.article>
  );
}

/** Editable pieces shared by the original cards and the integrated V2 plan. */
export function ActionFields({ action, index, editor }: FieldProps) {
  if (!editor) return null;
  return (
    <fieldset className="transaction-fields" disabled={editor.disabled}>
      <legend className="sr-only">Edit action {index + 1}</legend>
      <RecipientControl action={action} index={index} editor={editor} />
      <ValueControls action={action} index={index} editor={editor} />
      <FundingControl action={action} index={index} editor={editor} />
      {editor.error && (
        <p className="error-text" role="alert">
          {editor.error}
        </p>
      )}
    </fieldset>
  );
}

type FieldProps = Pick<Parameters<typeof ActionCard>[0], "action" | "index" | "editor">;

function RecipientControl({ action, index, editor }: FieldProps) {
  if (!editor) return null;
  return (
    <>
      {action.type === "transfer" && (
        <label>
          Recipient
          <select
            aria-label={`Recipient for action ${index + 1}`}
            value={editor.edits[index]?.to_payee_id ?? action.to_payee_id}
            onChange={(e) => editor.onEdit(index, "to_payee_id", e.target.value)}
          >
            {!editor.accounts?.payees.some((p) => p.payee_id === action.to_payee_id) && (
              <option value={action.to_payee_id}>{action.to_payee_label} - inferred from draft</option>
            )}
            {editor.accounts?.payees.map((p) => (
              <option key={p.payee_id} value={p.payee_id}>
                {p.label}
              </option>
            ))}
          </select>
          <small>Listed identity; final recipient verification is required by the bank.</small>
        </label>
      )}
    </>
  );
}

function ValueControls({ action, index, editor }: FieldProps) {
  if (!editor || action.type === "fx_conversion") return null;
  return (
    <>
      {action.type !== "stock_order" ? (
        <label className="amount-field">
          Amount <span>{action.currency}</span>
          <input
            aria-label={`Amount for action ${index + 1}`}
            inputMode="decimal"
            value={editor.edits[index]?.amount ?? action.amount}
            onChange={(e) => editor.onEdit(index, "amount", e.target.value)}
          />
        </label>
      ) : (
        <>
          <label className="amount-field">
            Shares
            <input
              aria-label={`Quantity for action ${index + 1}`}
              inputMode="numeric"
              value={editor.edits[index]?.quantity ?? String(action.quantity)}
              onChange={(e) => editor.onEdit(index, "quantity", e.target.value)}
            />
          </label>
          <label>
            Order type
            <select
              aria-label={`Order type for action ${index + 1}`}
              value={editor.edits[index]?.order_type ?? action.order_type}
              onChange={(e) => editor.onEdit(index, "order_type", e.target.value)}
            >
              <option value="market">Market order</option>
              <option value="limit">Limit order</option>
            </select>
          </label>
          {(editor.edits[index]?.order_type ?? action.order_type) === "limit" && (
            <label>
              Limit price ({action.currency})
              <input
                aria-label={`Limit price for action ${index + 1}`}
                inputMode="decimal"
                value={editor.edits[index]?.limit_price ?? action.limit_price ?? ""}
                onChange={(e) => editor.onEdit(index, "limit_price", e.target.value)}
                placeholder="Enter a price"
              />
            </label>
          )}
        </>
      )}
    </>
  );
}

function FundingControl({ action, index, editor }: FieldProps) {
  if (!editor || action.type === "fx_conversion" || action.type === "account_transfer") return null;
  return (
    <>
      <label>
        Source account
        <select
          aria-label={`Source account for action ${index + 1}`}
          value={editor.edits[index]?.from_account_id ?? action.from_account_id}
          onChange={(e) => editor.onEdit(index, "from_account_id", e.target.value)}
        >
          {!editor.accounts?.accounts.some((a) => a.account_id === action.from_account_id) && (
            <option value={action.from_account_id}>{action.from_account_label} - from draft</option>
          )}
          {editor.accounts?.accounts
            .filter((a) => a.currency === fromCurrency(action))
            .map((a) => (
              <option key={a.account_id} value={a.account_id}>
                {a.label}
              </option>
            ))}
        </select>
      </label>
    </>
  );
}
