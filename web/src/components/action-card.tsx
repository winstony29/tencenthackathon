"use client";

import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { TrendingUp, ReceiptText } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { Action, Quote } from "@/lib/contracts";
import { displayMoney, estimatedTotal } from "@/lib/money";

export function ActionCard({ action, index, quote }: { action: Action; index: number; quote?: Quote }) {
  const reducedMotion = useReducedMotion();
  const quoteLabel = quote ? `${quote.ticker}:${quote.currency}:${quote.price}` : "waiting";
  const reveal = { hidden: { opacity: 0, y: reducedMotion ? 0 : 18 }, visible: { opacity: 1, y: 0 } };
  const title =
    action.type === "transfer"
      ? "Send money"
      : action.type === "bill_payment"
        ? "Pay your bill"
        : `${action.side === "buy" ? "Buy" : "Sell"} shares`;
  const recipient =
    action.type === "transfer"
      ? action.to_payee_label
      : action.type === "bill_payment"
        ? action.biller_label
        : action.company_name;
  const usableQuote =
    action.type === "stock_order" && quote?.ticker === action.ticker && quote.currency === action.currency
      ? quote
      : undefined;
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
            <span className="badge">Review required</span>
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
              ) : (
                recipient.charAt(0)
              )}
            </span>
            <div>
              <span className="field-label">
                {action.type === "stock_order" ? action.ticker : action.type === "transfer" ? "To" : "Biller"}
              </span>
              <strong>{recipient}</strong>
            </div>
          </div>
          <div className="amount-block">
            <span className="field-label">{action.type === "stock_order" ? "Quantity" : "Amount"}</span>
            <p className="amount">
              {action.type === "stock_order" ? (
                <>
                  {action.quantity}
                  <span> shares</span>
                </>
              ) : (
                <>
                  <span>{action.currency} </span>
                  {displayMoney(action.amount, action.currency).slice(4)}
                </>
              )}
            </p>
          </div>
          <dl className="detail-list">
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
                    {usableQuote
                      ? displayMoney(estimatedTotal(usableQuote.price, action.quantity), action.currency)
                      : "—"}
                  </dd>
                </div>
              </>
            )}
            <div>
              <dt>{action.type === "stock_order" && action.side === "sell" ? "Pay into" : "Pay from"}</dt>
              <dd>{action.from_account_label}</dd>
            </div>
          </dl>
          {action.type === "stock_order" && (
            <p className="fine-print">Mock price. The execution price may differ from this estimate.</p>
          )}
        </CardContent>
      </Card>
    </motion.article>
  );
}
