"use client";
import { CalendarDays, PieChart } from "lucide-react";
import { cents, decimal, financialRows } from "@/lib/v4-financial";
import { displayMoney } from "@/lib/money";
import { Heading, Notice } from "./v4-primitives";
import type { ComponentProps } from "./v4-editors";

export function FinancialStatus({ state }: Pick<ComponentProps, "state">) {
  return (
    <div aria-live="polite">
      {financialRows(state).map((a) => {
        const message =
          a.remaining === null
            ? "Estimate unavailable."
            : a.remaining < 0n
              ? `${displayMoney(decimal(-a.remaining), a.currency)} shortfall for this plan.`
              : a.afterBills !== null && a.afterBills < 0n
                ? `${displayMoney(decimal(-a.afterBills), a.currency)} shortfall after upcoming bills.`
                : a.reserve !== null && a.afterBills !== null && a.afterBills < a.reserve
                  ? "This plan leaves less than your reserve after upcoming bills."
                  : null;
        return message ? (
          <Notice key={a.account_id} error>
            {a.label}: {message}
          </Notice>
        ) : null;
      })}
    </div>
  );
}

export function AccountInsights({ state, variant }: ComponentProps) {
  return (
    <>
      <Heading
        eyebrow="ACCOUNT CONTEXT"
        title="Keep room for what’s next"
        aside={<CalendarDays size={20} aria-hidden="true" />}
      />
      {financialRows(state)
        .filter((a) => a.context)
        .map((a) => {
          const spending = a.context!.spending;
          const total = spending?.categories.reduce((sum, c) => sum + cents(c.amount), 0n) ?? 0n;
          return (
            <section className="v4-insight" key={a.account_id} aria-label={`${a.label} context`}>
              <h3>{a.label}</h3>
              <dl className="v4-breakdown">
                <div>
                  <dt>After this plan</dt>
                  <dd>{a.projected === null ? "Unknown" : displayMoney(a.projected, a.currency)}</dd>
                </div>
                <div>
                  <dt>Bills through {a.context!.commitments?.through ?? "—"}</dt>
                  <dd>
                    {a.committed === undefined
                      ? "Not supplied"
                      : displayMoney(decimal(a.committed), a.currency)}
                  </dd>
                </div>
                <div>
                  <dt>After plan + bills</dt>
                  <dd>
                    {a.afterBills === null ? "Unknown" : displayMoney(decimal(a.afterBills), a.currency)}
                  </dd>
                </div>
                <div>
                  <dt>Your reserve</dt>
                  <dd>{a.reserve === null ? "Not set" : displayMoney(decimal(a.reserve), a.currency)}</dd>
                </div>
              </dl>
              {!!a.bills?.length && (
                <ol className="v4-timeline" aria-label="Upcoming bills">
                  {[...a.bills]
                    .sort((x, y) => x.due.localeCompare(y.due))
                    .map((b) => (
                      <li key={b.id}>
                        <time dateTime={b.due}>{b.due.slice(5)}</time>
                        <span>{b.label}</span>
                        <strong>{displayMoney(b.amount, a.currency)}</strong>
                      </li>
                    ))}
                </ol>
              )}
              {spending && (
                <details className="v4-spending" open={variant === "expanded" ? true : undefined}>
                  <summary>
                    <PieChart size={16} aria-hidden="true" /> Spending history{" "}
                    <span>{displayMoney(decimal(total), a.currency)}</span>
                  </summary>
                  <p className="v4-note">
                    {spending.from} – {spending.through} · supplied categories
                  </p>
                  <ul className="v4-spending-bars">
                    {spending.categories.map((c, i) => (
                      <li key={i}>
                        <div>
                          <span>{c.label}</span>
                          <strong>{displayMoney(c.amount, a.currency)}</strong>
                        </div>
                        <div className="v4-balance-track" aria-hidden="true">
                          <span
                            style={{
                              width: `${total > 0n ? Number((cents(c.amount) * 100n) / total) : 0}%`,
                            }}
                          />
                        </div>
                      </li>
                    ))}
                  </ul>
                  <p className="v4-note">Historical spending is not deducted again.</p>
                </details>
              )}
            </section>
          );
        })}
      <Notice>
        {state.handoff?.data.source === "mock" ? "Mock context. " : ""}Snapshot{" "}
        {state.handoff?.data.financial?.as_of}. Estimates exclude fees.
      </Notice>
    </>
  );
}
