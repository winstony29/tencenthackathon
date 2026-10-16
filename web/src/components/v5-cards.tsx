"use client";
import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import type { Composition } from "@/lib/v4-composition";
import type { State } from "@/lib/v4-state";
import { financialRows } from "@/lib/v4-financial";
import { displayMoney, estimatedTotal, fromCurrency } from "@/lib/money";
import { quoteSchema, validationSchema, type Validation, type Action, type Answer, type Quote, type RequestPlan } from "@/lib/contracts";
import { post, request } from "@/lib/api";
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
import { fallbackAnswerPresentation, acceptPlanView, fallbackPlanView, type PlanView, type AnswerPresentation } from "./v5-enquiry";
import { V5Payment } from "./v5-payment";
import { activityLabel } from "./v4-primitives";

const money = (value: string, currency: string) =>
  displayMoney(value, currency).replace(/^SGD /, "S$").replace(/^USD /, "US$");
const label = (value: string) => value.replace(/\.\.\.|â€¦/g, "··").replace(/…/g, "··");
function Task({
  action: a,
  quote,
  after,
  compact,
  flow = false,
  auditFields = [],
}: {
  action: Action;
  quote?: Quote;
  after?: string | null;
  compact: boolean;
  flow?: boolean;
  auditFields?: string[];
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
      {flow && a.type === "transfer" && (
        <figure className="v5-transfer-flow" aria-label="Transfer flow">
          <div>
            <small>From</small>
            <strong>{a.from_account_label}</strong>
          </div>
          <div>
            <strong>{money(a.amount, a.currency)}</strong>
            <span aria-hidden="true">&rarr;</span>
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
                : a.type === "account_transfer"
                  ? "Move money"
                  : "Bill payment"}
        </span>
      </div>
      <h2 className={`v5-figure ${auditFields.some(f => ["amount", "sell_amount", "quantity"].includes(f)) ? "v5-field-error" : ""}`}>
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
      {auditFields.length > 0 && <p className="v5-warning" role="alert">
        Check {auditFields.map(f => f.replaceAll("_", " ")).join(", ")}.
      </p>}
      <dl className="v5-facts">
        {a.type === "fx_conversion" ? (
          <>
            <div>
              <dt>From</dt>
              <dd className={auditFields.some(f => f.startsWith("from_account")) ? "v5-field-error" : ""}>{label(a.from_account_label)}</dd>
            </div>
            <div>
              <dt>Rate</dt>
              <dd>
                1 {a.sell_currency} = {a.rate} {a.buy_currency}
              </dd>
            </div>
            <div>
              <dt>Get</dt>
              <dd className={auditFields.some(f => f.startsWith("buy_")) ? "v5-field-error" : ""}>{money(a.buy_amount, a.buy_currency)}</dd>
            </div>
            <div>
              <dt>Into</dt>
              <dd className={auditFields.some(f => f.startsWith("to_account")) ? "v5-field-error" : ""}>{label(a.to_account_label)}</dd>
            </div>
          </>
        ) : (
          <div>
            <dt>{stock ? "Stock" : a.type === "bill_payment" ? "Biller" : "To"}</dt>
            <dd className={auditFields.some(f => /^(to_|biller|ticker|company)/.test(f)) ? "v5-field-error" : ""}>
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
            <dd className={auditFields.some(f => f.startsWith("from_account")) ? "v5-field-error" : ""}>{label(a.from_account_label)}</dd>
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
  const condition = h.draft.condition;
  const conditionQuote = condition ? h.data.quotes[condition.ticker] : undefined;
  const [settled, setSettled] = useState(false);
  const [completed, setCompleted] = useState(false);
  useEffect(() => {
    if (preview) return;
    const controller = new AbortController();
    const tickers = [
      ...new Set([...h.draft.actions.flatMap((a) => (a.type === "stock_order" ? [a.ticker] : []))]),
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
            (h.draft.actions.some(
              (a) => a.type === "stock_order" && a.ticker === ticker && a.currency === q.currency,
            ))
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
  const auditKey = JSON.stringify(draft);
  const [attempt, setAttempt] = useState(0);
  const [auditResult, setAuditResult] = useState<{key: string; attempt: number; value: Validation} | null>(null);
  const validation: Validation = auditResult?.key === auditKey && auditResult.attempt === attempt
    ? auditResult.value : {status: "pending", message: "Checking your request..."};
  useEffect(() => {
    if (preview) return;
    const controller = new AbortController();
    async function check() {
      try {
        let result: Validation;
        do {
          result = await post("/validate", validationSchema, {draft: JSON.parse(auditKey)}, controller.signal);
          if (result.status === "pending") await new Promise(resolve => setTimeout(resolve, 500));
        } while (result.status === "pending" && !controller.signal.aborted);
        if (!controller.signal.aborted) setAuditResult({key: auditKey, attempt, value: result});
      } catch {
        if (!controller.signal.aborted) setAuditResult({key: auditKey, attempt, value: {
          status: "unavailable", message: "Checking is unavailable. Retry before confirming.",
        }});
      }
    }
    void check();
    return () => controller.abort();
  }, [auditKey, preview, attempt]);
  const issues = "fields" in validation ? validation.fields : [];
  const fieldsFor = (index: number) => issues.filter(f => f.field.startsWith(`actions[${index}].`)).map(f => f.field.split(".")[1]);
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
            auditFields={fieldsFor(index)}
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
            auditFields={fieldsFor(i)}
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
      {condition && !completed && <section className="v5-tree-support" aria-label="Transfer condition">
        <p className="v5-caption">Only if</p>
        <h2>{condition.ticker} {{lt: "is below", lte: "is at most", gt: "is above", gte: "is at least", eq: "equals", ne: "does not equal"}[condition.operator]} {money(condition.threshold, condition.currency)}</h2>
        <p role="status">Condition met when prepared</p>
        {conditionQuote && <p className="v5-caption">{money(conditionQuote.price, conditionQuote.currency)} · {new Date(conditionQuote.as_of).toLocaleString("en-SG")}</p>}
      </section>}
      {composing && (
        <p className="v5-caption" role="status">
          Preparing your view...
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
      {!preview && <div className="v5-validation" role={issues.length ? "alert" : "status"}>
        {validation.status === "match" ? <p className="v5-caption">Request checked</p>
          : "message" in validation ? <p>{validation.message}</p>
          : <><p className="v5-warning">Check these details to continue.</p>
            {issues.map((f, i) => <p className="v5-field-error" key={i}>
              <strong>{f.field.replace(/^actions\[(\d+)\]\./, (_, n) => `Task ${Number(n) + 1}: `).replaceAll("_", " ")}</strong>: {f.note}
              {f.transcript_says && <span> Expected: {f.transcript_says}</span>}
            </p>)}
            <button className="v5-secondary" onClick={onCancel}>Correct request</button></>}
        {validation.status === "unavailable" && <button className="v5-secondary" onClick={() => setAttempt(n => n + 1)}>Retry check</button>}
      </div>}
      <V5Payment
        key={auditKey}
        validation={validation}
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
  payees: "Your recipients",
  help: "What I can do",
};
const day = (iso: string) => new Date(iso).toLocaleDateString("en-SG", { timeZone: "Asia/Singapore", day: "numeric", month: "short" });

/** A read-only answer (CONTRACTS §3.1 answers): facts only, nothing to confirm or sign. */
export function V5Answer({ answer: a, onDone, presentation = fallbackAnswerPresentation }: {
  answer: Answer; onDone: () => void; presentation?: AnswerPresentation;
}) {
  const timeline = a.kind === "bills" && presentation.variant === "timeline";
  const cards = ["balance", "holdings", "quote", "payees"].includes(a.kind) && presentation.variant === "cards";
  const totals = new Map<string, bigint>();
  if (presentation.summary && (a.kind === "balance" || a.kind === "holdings")) {
    const values = a.kind === "balance" ? a.accounts.map(x => ({currency: x.currency, value: x.balance})) : a.holdings;
    values.forEach(x => totals.set(x.currency, (totals.get(x.currency) ?? 0n) + BigInt(x.value.replace(".", ""))));
  }
  const rows: [string, string][] =
    a.kind === "balance"
      ? a.accounts.map((x) => [label(x.label), money(x.balance, x.currency)])
      : a.kind === "transactions"
        ? a.transactions.map((t) => [
            `${day(t.time)} · ${activityLabel(t.description)}`,
            `${t.direction === "credit" ? "+" : "−"}${money(t.amount, t.currency)}`,
          ])
        : a.kind === "bills"
          ? (timeline ? [...a.bills].sort((x, y) => Date.parse(x.due_at) - Date.parse(y.due_at)) : a.bills).map((b) => [
              `${activityLabel(label(b.label))} · ${b.recurrence ? `${b.recurrence}, next ` : ""}due ${day(b.due_at)}`,
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
    <section className="v5-task v5-answer" data-source={presentation.source} data-variant={timeline ? "timeline" : cards ? "cards" : "list"} aria-label={answerTitles[a.kind]}>
      <div className="v5-task-title">
        <span>{answerTitles[a.kind]}</span>
      </div>
      {[...totals].map(([currency, amount]) => <p className="v5-enquiry-number" key={currency}>
        {money(`${amount / 100n}.${(amount % 100n).toString().padStart(2, "0")}`, currency)}
      </p>)}
      {a.kind === "help" ? (
        <p>{a.message}</p>
      ) : rows.length ? (
        <>
          <div className={cards ? "v5-tree v5-tree-split" : ""}>
            {rows.slice(0, presentation.detail === "compact" ? 5 : undefined).map(([name, value], i) => (
              <div key={i} className={`v5-enquiry-row${timeline ? " v5-timeline-stop" : ""}${cards ? " v5-tree-support" : ""}`}>
                <span>{name}</span><strong>{value}</strong>
              </div>
            ))}
          </div>
          {presentation.detail === "compact" && rows.length > 5 && <details>
            <summary>Show all ({rows.length})</summary>
            {rows.slice(5).map(([name, value], i) => <div key={i} className={`v5-enquiry-row${timeline ? " v5-timeline-stop" : ""}`}>
              <span>{name}</span><strong>{value}</strong>
            </div>)}
          </details>}
        </>
      ) : (
        <p>No results found.</p>
      )}
      {(a.kind === "holdings" || a.kind === "quote") && (
        <p className="v5-caption">Prices may change. Fees excluded.</p>
      )}
      <button className="v4-button" onClick={onDone}>
        New request
      </button>
    </section>
  );
}


export function V5Plan({plan, view, onDone}: {plan: RequestPlan; view: PlanView; onDone: () => void}) {
  let checked: PlanView;
  try { checked = acceptPlanView(view, plan.actions.length, !!plan.condition.quote); }
  catch { checked = fallbackPlanView(plan.actions.length, !!plan.condition.quote); }
  const c = plan.condition;
  const status = {met: "Met at quoted price", not_met: "Not met at quoted price", stale: "Quote needs updating", unknown: "Not evaluated"}[c.state];
  const comparison = c.operator ? {lt: "below", lte: "at or below", gt: "above", gte: "at or above", eq: "equal to", ne: "not equal to"}[c.operator] : "";
  return <div className={`v5-plan is-${checked.layout}`} aria-label="Conditional request">
    <header className="v5-plan-heading"><p className="v5-card-label">Your conditional request</p>
      <h2>First this. Then that.</h2></header>
    {checked.nodes.map(n => {
      const key = `${n.component}:${n.binding}`;
      if (n.component === "Condition") return <section className={`v5-condition is-${n.variant}`} key={key} aria-label="Condition">
        <span className="v5-card-label">{c.operator ? "Only if" : "Condition"}</span>
        <h3>{c.ticker && c.threshold && c.currency ? <>{c.ticker} is {comparison} <strong>{money(c.threshold, c.currency)}</strong></> : c.text}</h3>
        <span className="v5-condition-state" data-state={c.state}>{status}</span>
        {c.quote && c.currency && c.quote.currency !== c.currency && <p>The quote and threshold use different currencies.</p>}
      </section>;
      if (n.component === "QuoteMetric" && c.quote) return <section className={`v5-quote-metric is-${n.variant}`} key={key} aria-label="Quoted stock price">
        <span>{c.quote.ticker} <small>quoted price</small></span><strong>{money(c.quote.price, c.quote.currency)}</strong>
        <time dateTime={c.quote.as_of}>{new Date(c.quote.as_of).toLocaleString("en-SG", {dateStyle: "medium", timeStyle: "short"})}</time>
      </section>;
      if (n.component === "Relationship") return <div className="v5-plan-connector" key={key}>
        <span aria-hidden="true">↓</span><span>{!c.operator ? "Then" : "Then, if met"}</span>
      </div>;
      if (n.component === "PlannedAction") return <div className="v5-planned-action" key={key}>
        <Task action={plan.actions[Number(n.binding)]} compact={n.variant === "compact"} />
      </div>;
      if (n.component === "AvailabilityNotice") return <section className="v5-plan-availability" key={key} role="status">
        <p>{plan.limitation}</p>
        {plan.unresolved.length > 0 && <ul>{plan.unresolved.map((u, i) => <li key={i}>{u}</li>)}</ul>}
      </section>;
      return null;
    })}
    <details className="v5-plan-original"><summary>Original request</summary><p>{plan.request}</p></details>
    <button className="v4-button secondary" onClick={onDone}>New request</button>
  </div>;
}
