"use client";
import { useState } from "react";
import { z } from "zod";
import { displayMoney } from "@/lib/money";
const money = z.string().regex(/^-?\d+\.\d{2}$/);
const period = z.object({ from: z.string(), through: z.string(), total: money, count: z.number().int() });
const item = z.object({
  occurrence_id: z.string(),
  label: z.string(),
  amount: money,
  currency: z.string(),
  due_at: z.string(),
});
const forecast = z.union([
  z.object({ account_id: z.string(), unavailable: z.literal(true) }),
  z.object({
    account_id: z.string(),
    currency: z.string(),
    window_end: z.string(),
    current_balance: money,
    projected_balance_before: money,
    commitment_total: money,
    scheduled_income_total: money,
    commitments: z.array(item),
  }),
]);
const component = z.enum([
  "EnquiryWorkspace",
  "BalanceSummary",
  "AccountList",
  "UpcomingBills",
  "TransactionList",
  "SpendingBreakdown",
  "PeriodComparison",
  "CashflowSummary",
]);
export const enquirySchema = z
  .object({
    topic: z.enum(["balances", "bills", "transactions", "spending", "overview"]),
    source: z.enum(["jev", "fallback"]),
    latency_ms: z.number(),
    data: z.object({
      source: z.literal("synthetic"),
      as_of: z.string(),
      accounts: z.array(
        z.object({ account_id: z.string(), label: z.string(), currency: z.string(), balance: money }),
      ),
      transactions: z
        .array(
          z.object({
            id: z.string(),
            account_id: z.string(),
            amount: money,
            currency: z.string(),
            direction: z.enum(["debit", "credit"]),
            time: z.string(),
            description: z.string(),
            category: z.string(),
          }),
        )
        .nullable(),
      forecasts: z.array(forecast),
      spending: z.array(z.object({ currency: z.string(), current: period, previous: period })).nullable(),
    }),
    spec: z.object({
      root: z.string(),
      elements: z.record(
        z.string(),
        z.object({
          type: component,
          variant: z.enum(["focus", "split", "compact", "expanded", "timeline", "waterfall"]),
          children: z.array(z.string()),
        }),
      ),
    }),
  })
  .superRefine((value, ctx) => {
    const root = value.spec.elements[value.spec.root];
    const ids = root?.children ?? [];
    const types = ids.map((id) => value.spec.elements[id]?.type);
    const required = {
      balances: ["BalanceSummary", "AccountList"],
      bills: ["UpcomingBills", "CashflowSummary"],
      transactions: ["TransactionList"],
      spending: ["SpendingBreakdown", "PeriodComparison"],
      overview: ["BalanceSummary", "UpcomingBills", "SpendingBreakdown", "PeriodComparison"],
    }[value.topic];
    if (
      !root ||
      root.type !== "EnquiryWorkspace" ||
      !["focus", "split"].includes(root.variant) ||
      ids.length > 7 ||
      new Set(ids).size !== ids.length ||
      new Set(types).size !== types.length ||
      required.some((t) => !types.includes(t as (typeof types)[number])) ||
      Object.keys(value.spec.elements).length !== ids.length + 1 ||
      ids.some(
        (id) =>
          !value.spec.elements[id] ||
          value.spec.elements[id].type === "EnquiryWorkspace" ||
          value.spec.elements[id].children.length > 0 ||
          !(
            ["compact", "expanded"].includes(value.spec.elements[id].variant) ||
            (value.spec.elements[id].type === "UpcomingBills" &&
              value.spec.elements[id].variant === "timeline" &&
              value.data.forecasts.some((f) => !("unavailable" in f) && f.commitments.length > 0)) ||
            (value.spec.elements[id].type === "CashflowSummary" &&
              value.spec.elements[id].variant === "waterfall" &&
              value.data.forecasts.some((f) => !("unavailable" in f)))
          ),
      )
    ) {
      ctx.addIssue({ code: "custom", message: "Invalid enquiry tree" });
    }
  });
export type Enquiry = z.infer<typeof enquirySchema>;
const cents = (s: string) => BigInt(s.replace(".", ""));
const decimal = (n: bigint) =>
  `${n < 0n ? "-" : ""}${(n < 0n ? -n : n) / 100n}.${((n < 0n ? -n : n) % 100n).toString().padStart(2, "0")}`;
const date = (s: string) =>
  new Date(s).toLocaleDateString("en-SG", { timeZone: "Asia/Singapore", day: "numeric", month: "short" });
const names = {
  BalanceSummary: "Your balances",
  AccountList: "Your accounts",
  UpcomingBills: "Upcoming bills",
  TransactionList: "Recent transactions",
  SpendingBreakdown: "Spending breakdown",
  PeriodComparison: "Month comparison",
  CashflowSummary: "After scheduled bills",
  EnquiryWorkspace: "",
};
function Empty({ children }: { children: React.ReactNode }) {
  return <p className="v5-caption">{children}</p>;
}
export function V5Enquiry({ value }: { value: Enquiry }) {
  const [account, setAccount] = useState("all");
  const [direction, setDirection] = useState("all");
  const data = value.data,
    root = value.spec.elements[value.spec.root];
  function content(type: z.infer<typeof component>, expanded: boolean, variant: string) {
    switch (type) {
      case "BalanceSummary": {
        const totals = new Map<string, bigint>();
        data.accounts.forEach((a) =>
          totals.set(a.currency, (totals.get(a.currency) ?? 0n) + cents(a.balance)),
        );
        return (
          <>
            {[...totals].map(([currency, total]) => (
              <p className="v5-enquiry-number" key={currency}>
                {displayMoney(decimal(total), currency)}
              </p>
            ))}
            {!totals.size && <Empty>No accounts available.</Empty>}
          </>
        );
      }
      case "AccountList":
        return (
          <div>
            {data.accounts.map((a) => (
              <div className="v5-enquiry-row" key={a.account_id}>
                <span>{a.label}</span>
                <strong>{displayMoney(a.balance, a.currency)}</strong>
              </div>
            ))}
          </div>
        );
      case "UpcomingBills": {
        const bills = data.forecasts.flatMap((f) => ("unavailable" in f ? [] : f.commitments));
        return (
          <>
            <Empty>Next 30 days from {date(data.as_of)}</Empty>
            {data.forecasts.some((f) => "unavailable" in f) && (
              <Empty>Some account schedules are unavailable.</Empty>
            )}
            {!bills.length && <Empty>No upcoming bills in the available schedules.</Empty>}
            {bills
              .sort((a, b) => a.due_at.localeCompare(b.due_at))
              .map((b) => (
                <div
                  className={variant === "timeline" ? "v5-enquiry-row v5-timeline-stop" : "v5-enquiry-row"}
                  key={b.occurrence_id}
                >
                  <span>
                    <small>{date(b.due_at)}</small>
                    {b.label}
                  </span>
                  <strong>{displayMoney(b.amount, b.currency)}</strong>
                </div>
              ))}
          </>
        );
      }
      case "CashflowSummary":
        return (
          <>
            {data.forecasts.map((f) => (
              <div key={f.account_id}>
                <p>{data.accounts.find((a) => a.account_id === f.account_id)?.label}</p>
                {"unavailable" in f ? (
                  <Empty>Schedule unavailable.</Empty>
                ) : (
                  <>
                    <p className="v5-enquiry-number">
                      {displayMoney(f.projected_balance_before, f.currency)}
                    </p>
                    {variant === "waterfall" && (
                      <CashflowWaterfall
                        balance={f.current_balance}
                        income={f.scheduled_income_total}
                        bills={f.commitment_total}
                        currency={f.currency}
                      />
                    )}
                    <Empty>
                      Estimate at {date(f.window_end)} - scheduled income of{" "}
                      {displayMoney(f.scheduled_income_total, f.currency)} and recorded bills of{" "}
                      {displayMoney(f.commitment_total, f.currency)}.
                    </Empty>
                    {cents(f.projected_balance_before) < 0n && (
                      <p className="v5-warning">Projected shortfall</p>
                    )}
                  </>
                )}
              </div>
            ))}
            <Empty>Other spending is excluded. Funds may be needed before income arrives.</Empty>
          </>
        );
      case "TransactionList": {
        if (data.transactions === null) return <Empty>Transaction history unavailable.</Empty>;
        const transactions = data.transactions.filter(
          (t) =>
            (account === "all" || t.account_id === account) &&
            (direction === "all" || t.direction === direction),
        );
        return (
          <>
            <div className="v5-enquiry-filters">
              <label>
                Account
                <select value={account} onChange={(e) => setAccount(e.target.value)}>
                  <option value="all">All accounts</option>
                  {data.accounts.map((a) => (
                    <option key={a.account_id} value={a.account_id}>
                      {a.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Direction
                <select value={direction} onChange={(e) => setDirection(e.target.value)}>
                  <option value="all">All</option>
                  <option value="debit">Money out</option>
                  <option value="credit">Money in</option>
                </select>
              </label>
            </div>
            {!transactions.length && <Empty>No matching records.</Empty>}
            {transactions.slice(0, expanded ? 50 : 5).map((t) => (
              <div className="v5-enquiry-row" key={t.id}>
                <span>
                  <small>{date(t.time)}</small>
                  {t.description}
                </span>
                <strong>
                  {t.direction === "debit" ? "-" : "+"}
                  {displayMoney(t.amount, t.currency)}
                </strong>
              </div>
            ))}
            {!expanded && transactions.length > 5 && (
              <details>
                <summary>More transactions ({transactions.length - 5})</summary>
                {transactions.slice(5).map((t) => (
                  <div className="v5-enquiry-row" key={t.id}>
                    <span>
                      {date(t.time)} - {t.description}
                    </span>
                    <strong>
                      {t.direction === "debit" ? "-" : "+"}
                      {displayMoney(t.amount, t.currency)}
                    </strong>
                  </div>
                ))}
              </details>
            )}
          </>
        );
      }
      case "SpendingBreakdown":
      case "PeriodComparison":
        if (data.spending === null) return <Empty>Spending history unavailable.</Empty>;
        return (
          <>
            {data.spending.map((s) => (
              <div key={s.currency}>
                <h3>{s.currency}</h3>
                {type === "SpendingBreakdown" ? (
                  <>
                    <Empty>
                      {date(s.current.from)} to {date(s.current.through)} - categories are not supplied by the
                      bank.
                    </Empty>
                    <div className="v5-enquiry-row">
                      <span>Uncategorised outflows</span>
                      <strong>{displayMoney(s.current.total, s.currency)}</strong>
                    </div>
                  </>
                ) : (
                  <>
                    {[
                      ["Previous month", s.previous],
                      ["This month so far", s.current],
                    ].map(([title, p]) => {
                      const period = p as typeof s.current;
                      const max = [cents(s.current.total), cents(s.previous.total)].reduce(
                        (a, b) => (a > b ? a : b),
                        1n,
                      );
                      return (
                        <div key={String(title)} className="v5-bars">
                          <div className="v5-enquiry-row">
                            <span>
                              {String(title)}
                              <small>
                                {date(period.from)} to {date(period.through)}
                              </small>
                            </span>
                            <strong>{displayMoney(period.total, s.currency)}</strong>
                          </div>
                          <div className="v5-track" aria-hidden="true">
                            <i
                              style={{
                                width: `${Number((cents(period.total) * 100n) / max)}%`,
                                background: "#c8102e",
                              }}
                            />
                          </div>
                        </div>
                      );
                    })}
                    <Empty>Partial month versus a full month; not a like-for-like trend.</Empty>
                  </>
                )}
              </div>
            ))}
          </>
        );
    }
  }
  return (
    <div className="v5-card-content" data-source={value.source}>
      <p className="v5-caption">
        Synthetic bank data - {date(data.as_of)}
        {value.source === "fallback" ? " - standard view" : ""}
      </p>
      <div className={`v5-tree v5-tree-${root.variant}`}>
        {root.children.map((id) => {
          const n = value.spec.elements[id];
          return (
            <section key={id} className="v5-tree-support" data-component={n.type}>
              <h2>{names[n.type]}</h2>
              {content(n.type, n.variant === "expanded", n.variant)}
            </section>
          );
        })}
      </div>
      <p className="v5-caption">
        History contains sample records, not a complete bank statement. Transfers and investments may be
        included in outflows.
      </p>
    </div>
  );
}

export function CashflowWaterfall({
  balance,
  income,
  bills,
  currency,
}: {
  balance: string;
  income: string;
  bills: string;
  currency: string;
}) {
  const b = cents(balance),
    i = cents(income),
    expense = cents(bills),
    end = b + i - expense;
  const steps = [
    { label: "Now", from: 0n, to: b, value: b },
    { label: "Income", from: b, to: b + i, value: i },
    { label: "Bills", from: b + i, to: end, value: -expense },
    { label: "After", from: 0n, to: end, value: end },
  ];
  const values = [0n, b, b + i, end],
    min = values.reduce((a, v) => (v < a ? v : a)),
    max = values.reduce((a, v) => (v > a ? v : a));
  const range = max - min || 1n;
  const y = (n: bigint) => 120 - Number(((n - min) * 100n) / range);
  return (
    <figure className="v5-waterfall" aria-label="Estimated cashflow waterfall">
      <svg
        viewBox="0 0 320 154"
        role="img"
        aria-label="Current balance plus scheduled income minus bills equals projected balance"
      >
        <line x1="8" x2="310" y1={y(0n)} y2={y(0n)} stroke="#89998e" strokeDasharray="3 3" />
        {steps.map((s, index) => (
          <g key={s.label}>
            <rect
              x={index * 78 + 16}
              y={Math.min(y(s.from), y(s.to))}
              width="48"
              height={Math.max(1, Math.abs(y(s.from) - y(s.to)))}
              rx="3"
              fill={s.value < 0n ? "#c8102e" : "#28754e"}
            />
            <text x={index * 78 + 40} y="145" textAnchor="middle" fontSize="11" fill="currentColor">
              {s.label}
            </text>
          </g>
        ))}
      </svg>
      <figcaption>
        {steps.map((s) => (
          <div className="v5-enquiry-row" key={s.label}>
            <span>{s.label}</span>
            <strong>{displayMoney(decimal(s.value), currency)}</strong>
          </div>
        ))}
      </figcaption>
    </figure>
  );
}


// Live answers retain their upstream data; these choices affect presentation only.
export const answerPresentationSchema = z.strictObject({
  source: z.enum(["jev", "fallback"]),
  variant: z.enum(["list", "cards", "timeline"]),
  detail: z.enum(["compact", "expanded"]),
  summary: z.boolean(),
  latency_ms: z.number().nonnegative(),
});
export type AnswerPresentation = z.infer<typeof answerPresentationSchema>;
export const fallbackAnswerPresentation: AnswerPresentation = {
  source: "fallback", variant: "list", detail: "compact", summary: false, latency_ms: 0,
};
