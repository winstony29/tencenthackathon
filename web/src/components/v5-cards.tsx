"use client";
import { useEffect, useState } from "react";
import { ArrowUpRight, Check, ChevronDown } from "lucide-react";
import type { Composition } from "@/lib/v4-composition";
import type { State } from "@/lib/v4-state";
import { cents, decimal, financialRows } from "@/lib/v4-financial";
import { displayMoney, estimatedTotal, fromCurrency } from "@/lib/money";
import { quoteSchema, type Action, type Answer, type Quote } from "@/lib/contracts";
import { request } from "@/lib/api";
import { V5Payment } from "./v5-payment";

const money = (value: string, currency: string) =>
  displayMoney(value, currency).replace(/^SGD /, "S$").replace(/^USD /, "US$");
const label = (value: string) => value.replace(/\.\.\.|â€¦/g, "··").replace(/…/g, "··");
function Task({
  action: a,
  quote,
  after,
  compact,
}: {
  action: Action;
  quote?: Quote;
  after?: string | null;
  compact: boolean;
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
              : a.type === "account_transfer"
                ? "Move money"
                : "Bill payment"
      }
    >
      <div className="v5-task-title">
        <span>
          {stock
            ? `${a.side === "buy" ? "Buy" : "Sell"} shares`
            : fx
              ? "Convert currency"
              : a.type === "transfer"
                ? "Transfer"
                : a.type === "account_transfer"
                  ? "Move money"
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
            <dt>{stock ? "Stock" : a.type === "bill_payment" ? "Biller" : "To"}</dt>
            <dd>
              {a.type === "stock_order"
                ? `${a.ticker} · ${a.company_name}`
                : label(
                    a.type === "transfer"
                      ? a.to_payee_label
                      : a.type === "account_transfer"
                        ? a.to_account_label
                        : a.biller_label,
                  )}
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
}: {
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
  }, [h.draft]);
  const rows = financialRows({ ...state, handoff: { ...h, data: { ...h.data, quotes } } });
  const selected = new Set(composition.nodes.map((n) => n.recipe.component));
  const projection = selected.has("BalanceProjection");
  const compact = h.draft.actions.length > 1 || projection;
  const short = rows.find(
    (r) => (r.remaining !== null && r.remaining < 0n) || (r.afterBills !== null && r.afterBills < 0n),
  );
  const reserve = rows.find((r) => r.afterBills !== null && r.reserve !== null && r.afterBills < r.reserve);
  const context =
    selected.has("AccountInsights") ||
    selected.has("FundingOptions") ||
    selected.has("RecipientDetails") ||
    selected.has("StockEstimate");
  return (
    <div className="v5-card-content" data-source={composition.source}>
      {projection && (
        <section className="v5-projection" aria-label="Balance projection">
          <h2>Room for this?</h2>
          <p className={short || reserve ? "v5-warning" : "v5-verdict"}>
            {short
              ? "This would leave you short."
              : reserve
                ? "This would dip into your reserve."
                : rows.every((r) => r.afterBills !== null)
                  ? "Here’s what you’d have left."
                  : "Here’s the estimated impact."}
          </p>
          {rows.map((r) => {
            const bars = [
              { title: "Available", value: cents(r.balance), color: "#202823" },
              { title: "After this", value: r.remaining, color: "#c8102e" },
              ...(r.afterBills !== null
                ? [{ title: "After upcoming bills", value: r.afterBills, color: "#2f8a5f" }]
                : []),
            ];
            return (
              <div className="v5-bars" key={r.account_id}>
                <span className="v5-account-caption">{label(r.label)}</span>
                {bars.map((b) => (
                  <div className="v5-bar" key={b.title}>
                    <div>
                      <span>{b.title}</span>
                      <strong>
                        {b.value === null ? "Unavailable" : money(decimal(b.value), r.currency)}
                      </strong>
                    </div>
                    <div className="v5-track" aria-hidden="true">
                      <i
                        style={{
                          width: `${b.value === null || b.value < 0n || cents(r.balance) === 0n ? 0 : Number((b.value * 100n) / cents(r.balance))}%`,
                          maxWidth: "100%",
                          background: b.color,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            );
          })}
          <p className="v5-caption">
            Estimate · excludes fees{rows.some((r) => r.afterBills === null) ? " and unlisted bills" : ""}.
          </p>
        </section>
      )}
      <div className="v5-task-stack" hidden={completed}>
        {h.draft.actions.map((a, i) => (
          <Task
            key={i}
            action={a}
            compact={compact}
            quote={a.type === "stock_order" ? quotes[a.ticker] : undefined}
            after={rows.find((r) => r.account_id === a.from_account_id)?.projected}
          />
        ))}
      </div>
      {!projection && (short || reserve) && (
        <p className="v5-warning">
          {short
            ? "Your balance may not cover this and upcoming bills."
            : "This may dip into your reserve after upcoming bills."}
        </p>
      )}
      {context && (
        <details className="v5-support">
          <summary>
            Account details <ChevronDown size={14} />
          </summary>
          {rows.map((r) => (
            <div key={r.account_id}>
              <p>
                {label(r.label)} · {money(r.balance, r.currency)} available
              </p>
              {r.bills?.map((b) => (
                <p key={b.id}>
                  {b.label} · {b.due.slice(5)} <strong>{money(b.amount, r.currency)}</strong>
                </p>
              ))}
              {r.reserve !== null && (
                <p>
                  Reserve <strong>{money(decimal(r.reserve), r.currency)}</strong>
                </p>
              )}
            </div>
          ))}
        </details>
      )}
      {!projection && !settled && (
        <button className="v5-afford-link" disabled={locked} onClick={onAfford}>
          Can I afford it? <ArrowUpRight size={14} />
        </button>
      )}
      <V5Payment
        draft={h.draft}
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

const answerTitles: Record<Answer["kind"], string> = {
  balance: "Your balances",
  transactions: "Recent transactions",
  bills: "Upcoming bills",
  holdings: "Your shares",
  quote: "Share price",
  fx_rate: "Exchange rate",
  payees: "Who you can pay",
  help: "What I can do",
};
const day = (iso: string) => new Date(iso).toLocaleDateString("en-SG", { day: "numeric", month: "short" });

/** A read-only answer (CONTRACTS §3.1 answers): facts only, nothing to confirm or sign. */
export function V5Answer({ answer: a, onDone }: { answer: Answer; onDone: () => void }) {
  const rows: [string, string][] =
    a.kind === "balance"
      ? a.accounts.map((x) => [label(x.label), money(x.balance, x.currency)])
      : a.kind === "transactions"
        ? a.transactions.map((t) => [
            `${day(t.time)} · ${t.description}`,
            `${t.direction === "credit" ? "+" : "−"}${money(t.amount, t.currency)}`,
          ])
        : a.kind === "bills"
          ? a.bills.map((b) => [
              `${label(b.label)} · ${b.recurrence ? `${b.recurrence}, next ` : ""}due ${day(b.due_at)}`,
              money(b.amount, b.currency),
            ])
          : a.kind === "holdings"
            ? a.holdings.map((h) => [
                `${h.ticker} · ${h.quantity} shares`,
                `${money(h.value, h.currency)} est.`,
              ])
            : a.kind === "quote"
              ? a.quotes.map((q) => [`${q.ticker} · ${q.company_name}`, money(q.price, q.currency)])
              : a.kind === "fx_rate"
                ? [[`1 ${a.rate.from}`, `${a.rate.rate} ${a.rate.to}`]]
                : a.kind === "payees"
                  ? a.payees.map((p) => [p.name, label(p.label)])
                  : [];
  return (
    <section className="v5-task" aria-label={answerTitles[a.kind]}>
      <div className="v5-task-title">
        <span>{answerTitles[a.kind]}</span>
        <span>Nothing to sign</span>
      </div>
      {a.kind === "help" ? (
        <p>{a.message}</p>
      ) : rows.length ? (
        <dl className="v5-facts">
          {rows.map(([name, value], i) => (
            <div key={i}>
              <dt>{name}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p>Nothing to show for that.</p>
      )}
      {(a.kind === "holdings" || a.kind === "quote") && (
        <p className="v5-caption">Mock prices · estimates exclude fees.</p>
      )}
      <button className="v4-button" onClick={onDone}>
        New request
      </button>
    </section>
  );
}
