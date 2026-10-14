"use client";
import { useEffect, useState } from "react";
import { ArrowUpRight, Check } from "lucide-react";
import type { Composition } from "@/lib/v4-composition";
import type { State } from "@/lib/v4-state";
import { financialRows } from "@/lib/v4-financial";
import { displayMoney, estimatedTotal, fromCurrency } from "@/lib/money";
import { quoteSchema, type Action, type Quote } from "@/lib/contracts";
import { request } from "@/lib/api";
import {
  RecipientDetails,
  StockEstimate,
  FundingOptions,
  BalanceProjection,
  AmountSimulator,
} from "./v4-analysis";
import { AccountInsights } from "./v4-context";
import { effective, type Event } from "@/lib/v4-state";
import type { ReactNode } from "react";
import { V5Payment } from "./v5-payment";

const money = (value: string, currency: string) =>
  displayMoney(value, currency).replace(/^SGD /, "S$").replace(/^USD /, "US$");
const label = (value: string) => value.replace(/\.\.\.|â€¦/g, "··").replace(/…/g, "··");
function Task({
  action: a,
  quote,
  after,
  compact,
  flow = false,
}: {
  action: Action;
  quote?: Quote;
  after?: string | null;
  compact: boolean;
  flow?: boolean;
}) {
  const stock = a.type === "stock_order";
  const fx = a.type === "fx_conversion";
  const price = stock
    ? a.order_type === "limit"
      ? a.limit_price
      : quote?.currency === a.currency
        ? quote.price
        : null
    : null;
  return (
    <section
      className={`v5-task ${compact ? "is-compact" : ""}`}
      aria-label={
        stock
          ? "Stock order"
          : fx
            ? "Currency conversion"
            : a.type === "transfer"
              ? "Transfer details"
              : "Bill payment"
      }
    >
      {flow && a.type === "transfer" && (
        <figure className="v5-transfer-flow" aria-label="Transfer flow">
          <div>
            <small>From</small>
            <strong>{a.from_account_label}</strong>
          </div>
          <div>
            <strong>{money(a.amount, a.currency)}</strong>
            <span aria-hidden="true">?</span>
          </div>
          <div>
            <small>To</small>
            <strong>{a.to_payee_label}</strong>
          </div>
        </figure>
      )}
      <div className="v5-task-title">
        <span>
          {stock
            ? `${a.side === "buy" ? "Buy" : "Sell"} shares`
            : fx
              ? "Convert currency"
              : a.type === "transfer"
                ? "Transfer"
                : "Bill payment"}
        </span>
        <span>Not sent</span>
      </div>
      <h2 className="v5-figure">
        {stock ? (
          <>
            {a.quantity}
            <small> shares</small>
          </>
        ) : a.type === "fx_conversion" ? (
          money(a.sell_amount, a.sell_currency)
        ) : (
          money(a.amount, a.currency)
        )}
      </h2>
      <dl className="v5-facts">
        {a.type === "fx_conversion" ? (
          <>
            <div>
              <dt>From</dt>
              <dd>{label(a.from_account_label)}</dd>
            </div>
            <div>
              <dt>Rate</dt>
              <dd>
                1 {a.sell_currency} = {a.rate} {a.buy_currency}
              </dd>
            </div>
            <div>
              <dt>Get</dt>
              <dd>{money(a.buy_amount, a.buy_currency)}</dd>
            </div>
            <div>
              <dt>Into</dt>
              <dd>{label(a.to_account_label)}</dd>
            </div>
          </>
        ) : (
          <div>
            <dt>{stock ? "Stock" : a.type === "transfer" ? "To" : "Biller"}</dt>
            <dd>
              {a.type === "stock_order"
                ? `${a.ticker} · ${a.company_name}`
                : label(a.type === "transfer" ? a.to_payee_label : a.biller_label)}
            </dd>
          </div>
        )}
        {a.type === "bill_payment" && (
          <div>
            <dt>Reference</dt>
            <dd>{a.bill_reference}</dd>
          </div>
        )}
        {!fx && (
          <div>
            <dt>{stock && a.side === "sell" ? "Into" : "From"}</dt>
            <dd>{label(a.from_account_label)}</dd>
          </div>
        )}
        {stock && (
          <>
            <div>
              <dt>Order</dt>
              <dd>{a.order_type === "market" ? "Market" : `Limit · ${money(a.limit_price!, a.currency)}`}</dd>
            </div>
            <div>
              <dt>Price now · est.</dt>
              <dd>{quote ? money(quote.price, a.currency) : "Unavailable"}</dd>
            </div>
            <div>
              <dt>Total · est.</dt>
              <dd>{price ? money(estimatedTotal(price, a.quantity), a.currency) : "Unavailable"}</dd>
            </div>
          </>
        )}
        {!compact && after !== undefined && (
          <div className="v5-balance-row">
            <dt>Balance after{stock ? " · est." : ""}</dt>
            <dd>{after === null ? "Unavailable" : money(after, fromCurrency(a))}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}

export function V5Cards({
  state,
  composition,
  onCancel,
  onAfford,
  onLock,
  preview = false,
  composing = false,
  dispatch = () => {},
}: {
  preview?: boolean;
  composing?: boolean;
  dispatch?: (event: Event) => void;
  state: State;
  composition: Composition;
  onCancel: () => void;
  onAfford: () => void;
  onLock: (locked: boolean) => void;
}) {
  const h = state.handoff!;
  const [quotes, setQuotes] = useState<Record<string, Quote>>(h.data.quotes);
  const [locked, setLocked] = useState(false);
  const [settled, setSettled] = useState(false);
  const [completed, setCompleted] = useState(false);
  useEffect(() => {
    if (preview) return;
    const controller = new AbortController();
    const tickers = [
      ...new Set(h.draft.actions.flatMap((a) => (a.type === "stock_order" ? [a.ticker] : []))),
    ];
    async function refresh() {
      for (const ticker of tickers) {
        try {
          const q = await request(`/quote?ticker=${encodeURIComponent(ticker)}`, quoteSchema, {
            signal: controller.signal,
          });
          if (
            !controller.signal.aborted &&
            q.ticker === ticker &&
            h.draft.actions.some(
              (a) => a.type === "stock_order" && a.ticker === ticker && a.currency === q.currency,
            )
          )
            setQuotes((prev) => ({ ...prev, [ticker]: q }));
        } catch {
          /* Keep the last labelled estimate; never change the draft. */
        }
      }
    }
    void refresh();
    const timer = tickers.length ? setInterval(() => void refresh(), 10000) : undefined;
    return () => {
      controller.abort();
      if (timer) clearInterval(timer);
    };
  }, [h.draft, preview]);
  const rows = financialRows({
    ...state,
    handoff: { ...h, data: { ...h.data, quotes } },
  });
  const selected = new Set(composition.nodes.map((n) => n.recipe.component));
  const projection = selected.has("BalanceProjection");
  const viewState = {
    ...state,
    handoff: { ...h, data: { ...h.data, quotes } },
  };
  const values = effective(viewState);
  const draft = { ...h.draft, actions: values.actions };
  const root = composition.spec.elements[composition.spec.root];
  function render(id: string): ReactNode {
    const node = composition.nodes.find((n) => n.id === id)!;
    const { component, action, variant } = node.recipe;
    const element = composition.spec.elements[id];
    const props = { state: viewState, dispatch, action, variant };
    let content: ReactNode;
    switch (component) {
      case "TaskGroup":
      case "DecisionGroup":
        return (
          <section key={id} className={`v5-tree-group v5-group-${component}`} data-component={component}>
            {Object.values(element.slots ?? {})
              .flat()
              .map(render)}
          </section>
        );
      case "TaskEditor": {
        const index = action!;
        const a = draft.actions[index];
        content = (
          <Task
            action={a}
            compact={variant === "compact"}
            flow={variant === "flow"}
            quote={a.type === "stock_order" ? quotes[a.ticker] : undefined}
            after={rows.find((r) => r.account_id === a.from_account_id)?.projected}
          />
        );
        break;
      }
      case "BalanceProjection":
        content = <BalanceProjection {...props} />;
        break;
      case "AmountSimulator":
        content = <AmountSimulator {...props} />;
        break;
      case "RecipientDetails":
        content = <RecipientDetails {...props} />;
        break;
      case "StockEstimate":
        content = <StockEstimate {...props} />;
        break;
      case "FundingOptions":
        content = <FundingOptions {...props} />;
        break;
      case "AccountInsights":
        content = <AccountInsights {...props} />;
        break;
      case "TransactionReview":
        content = draft.actions.map((a, i) => (
          <Task
            key={i}
            action={a}
            compact={false}
            quote={a.type === "stock_order" ? quotes[a.ticker] : undefined}
          />
        ));
        break;
    }
    return (
      <section
        key={id}
        className={component === "TaskEditor" ? "" : "v5-tree-support"}
        aria-label={component === "BalanceProjection" ? "Balance projection" : undefined}
        data-component={component}
        data-variant={variant}
      >
        {content}
      </section>
    );
  }
  return (
    <div className="v5-card-content" data-source={composition.source}>
      {composing && (
        <p className="v5-caption" role="status">
          Preparing your view?
        </p>
      )}
      <div className={`v5-tree v5-tree-${composition.layout}`} hidden={completed}>
        {[...(root.slots?.primary ?? []), ...(root.slots?.tasks ?? []), ...(root.slots?.context ?? [])].map(
          render,
        )}
      </div>
      {!projection &&
        rows.some(
          (r) => (r.remaining !== null && r.remaining < 0n) || (r.afterBills !== null && r.afterBills < 0n),
        ) && <p className="v5-warning">Your balance may not cover this and upcoming bills.</p>}
      {!projection && !settled && (
        <button className="v5-afford-link" disabled={locked || composing} onClick={onAfford}>
          Can I afford it? <ArrowUpRight size={14} />
        </button>
      )}
      <V5Payment
        preview={preview || composing || Object.keys(values.errors).length > 0}
        draft={draft}
        onSettled={(done) => {
          setSettled(true);
          setCompleted(done);
        }}
        onCancel={onCancel}
        onLock={(value) => {
          setLocked(value);
          onLock(value);
        }}
      />
      <p className="v5-caption v5-secure">
        <Check size={12} /> Sandbox bank · no real money
      </p>
    </div>
  );
}
