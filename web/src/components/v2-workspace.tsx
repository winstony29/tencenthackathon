"use client";
import { ActionFields } from "./action-card";
import type { ConversationEvent, ConversationState } from "@/lib/v2-conversation";
import type { Action } from "@/lib/contracts";
import type { V2Manifest } from "@/lib/v2-presentation";
import { editedActions, projectedBalances } from "@/lib/draft-editor";
import { motion, useReducedMotion } from "motion/react";
import { displayMoney, estimatedTotal } from "@/lib/money";
import { exampleQuote } from "@/lib/examples";
import { v2Accounts } from "@/lib/v2-parser";
import { winstonFixtures } from "@/lib/examples";
import { Button } from "./ui/button";

export function WinstonPlaceholder({
  selected,
  onSelect,
  onCompose,
  busy,
}: {
  selected: string;
  onSelect: (id: string) => void;
  onCompose: () => void;
  busy: boolean;
}) {
  return (
    <section className="v2-winston-placeholder" aria-label="Mock Winston output">
      <div>
        <p className="eyebrow">MOCK WINSTON OUTPUT</p>
        <p>Choose a resolved demo task.</p>
      </div>
      <label>
        Resolved output
        <select
          aria-label="Mock Winston output scenario"
          value={selected}
          disabled={busy}
          onChange={(e) => onSelect(e.target.value)}
        >
          {winstonFixtures.map((f) => (
            <option key={f.id} value={f.id}>
              {f.label}
            </option>
          ))}
        </select>
      </label>
      <Button onClick={onCompose} disabled={busy}>
        {busy ? "Asking JEV…" : "Compose with live JEV"}
      </Button>
    </section>
  );
}

export function V2Workspace({
  state,
  manifest,
  dispatch,
}: {
  state: ConversationState;
  manifest: V2Manifest;
  dispatch: (event: ConversationEvent) => void;
}) {
  const reducedMotion = useReducedMotion();
  if (!state.draft) return null;
  const edited = editedActions(state.draft, state.edits, v2Accounts);
  const valid = !Object.keys(edited.errors).length;
  const projections = valid ? projectedBalances(edited.actions, v2Accounts, { AAPL: exampleQuote }) : [];
  const review = state.surface === "review";
  const support = [...manifest.supporting].sort((a, b) =>
    a ===
    (manifest.focus === "affordability"
      ? "projection"
      : manifest.focus === "recipient"
        ? "recipient"
        : "accounts")
      ? -1
      : b === "projection" && manifest.focus === "affordability"
        ? 1
        : 0,
  );
  return (
    <div
      className={`v2-workspace v2-layout-${manifest.layout} v2-density-${manifest.density}`}
      data-focus={manifest.focus}
      data-bundle={manifest.bundle}
    >
      <div className="v2-task-grid">
        <div className="v2-plan-heading">
          <p className="eyebrow">
            PLAN · {edited.actions.length} {edited.actions.length === 1 ? "TASK" : "TASKS"}
          </p>
        </div>
        {manifest.workspace?.tasks
          .slice()
          .sort((a, b) => a.priority - b.priority)
          .map(({ action: index }) => {
            const action = edited.actions[index];
            const account = projections.find((p) => p.account_id === action.from_account_id);
            const price =
              action.type === "stock_order" && action.order_type === "limit"
                ? action.limit_price
                : action.type === "stock_order" &&
                    action.ticker === exampleQuote.ticker &&
                    action.currency === exampleQuote.currency
                  ? exampleQuote.price
                  : null;
            const estimate =
              action.type === "stock_order"
                ? price
                  ? estimatedTotal(price, action.quantity)
                  : null
                : action.amount;
            return (
              <motion.section
                key={index}
                className="v2-plan-task"
                aria-labelledby={`task-${index}`}
                layout={reducedMotion ? false : "position"}
                transition={{ duration: reducedMotion ? 0 : 0.24, ease: "easeInOut" }}
              >
                <header className="v2-plan-task-heading">
                  <span className="v2-task-number">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <p className="eyebrow">
                      {action.type === "stock_order"
                        ? `${action.side} · ${action.ticker}`
                        : action.type === "transfer"
                          ? "TRANSFER"
                          : "BILL PAYMENT"}
                    </p>
                    <h2 id={`task-${index}`}>
                      {action.type === "stock_order"
                        ? action.company_name
                        : action.type === "transfer"
                          ? action.to_payee_label
                          : action.biller_label}
                    </h2>
                  </div>
                  <span className="v2-task-state">{review ? "Review" : "Editable"}</span>
                </header>
                <div className="v2-task-body">
                  <ActionFields
                    action={action}
                    index={index}
                    editor={{
                      accounts: v2Accounts,
                      edits: state.edits,
                      error: edited.errors[index],
                      approved: !!state.approved[index],
                      disabled: review,
                      showApproval: false,
                      onEdit: (index, field, value) => dispatch({ type: "edit", index, field, value }),
                      onApprove: () => {},
                    }}
                  />
                  <div className="v2-task-impact" aria-label={`Impact of action ${index + 1}`}>
                    <p className="eyebrow">{action.type === "stock_order" ? "ORDER ESTIMATE" : "PAYMENT"}</p>
                    <strong>
                      {valid && estimate ? displayMoney(estimate, action.currency) : "Check details"}
                    </strong>
                    {action.type === "bill_payment" && <small>Reference {action.bill_reference}</small>}
                    {action.type === "stock_order" && (
                      <small>
                        {action.order_type === "limit" ? "Limit price" : "Mock market price"} ·{" "}
                        {price ? displayMoney(price, action.currency) : "Unavailable"} / share. Excludes fees
                        and taxes. Final price may vary.
                      </small>
                    )}
                    {!review && account && manifest.supporting.includes("projection") && (
                      <div className="v2-linked-balance">
                        <span>After all account tasks</span>
                        <b className={account.projected?.startsWith("-") ? "error-text" : ""}>
                          {account.projected
                            ? displayMoney(account.projected, account.currency)
                            : "Price unavailable"}
                        </b>
                      </div>
                    )}
                    <small>
                      {action.from_account_label} · {action.currency}
                    </small>
                    {!review && valid && manifest.tools.includes("slider") && (
                      <SimulationControl actions={edited.actions} target={index} dispatch={dispatch} />
                    )}
                  </div>
                </div>
              </motion.section>
            );
          })}
      </div>
      {!review && (
        <aside className="v2-support" aria-label="Context for your task">
          {support.map((component) => (
            <section
              key={component}
              className={`v2-context ${component === "projection" || (component === "recipient" && manifest.focus === "recipient") ? "v2-context-primary" : ""}`}
            >
              <p className="eyebrow">
                {component === "projection"
                  ? "PROJECTED BALANCE"
                  : component === "accounts"
                    ? "MOCK ACCOUNTS"
                    : component === "recipient"
                      ? "RECIPIENT CHECK"
                      : "HOLDINGS"}
              </p>
              {component === "projection" ? (
                <>
                  <h2>{edited.actions.length > 1 ? "Room for both?" : "After this task"}</h2>
                  {!valid ? (
                    <p>Fix highlighted fields to see estimates.</p>
                  ) : (
                    projections.map((account) => {
                      const negative = account.projected?.startsWith("-");
                      return (
                        <div key={account.account_id} className="v2-balance-row">
                          <span>{account.label}</span>
                          <strong className={negative ? "error-text" : ""}>
                            {account.projected
                              ? displayMoney(account.projected, account.currency)
                              : "Price unavailable"}
                          </strong>
                          <small>{negative ? "Insufficient funds" : "Estimated balance"}</small>
                          {manifest.tools.includes("chart") && account.projected && (
                            <div className="v2-balance-chart" aria-hidden="true">
                              <span
                                style={{
                                  width: `${Math.max(0, Math.min(100, Number((BigInt(account.projected.replace(".", "")) * 100n) / (BigInt(account.balance.replace(".", "")) || 1n))))}%`,
                                }}
                              />
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                  <p className="fine-print">
                    Currencies shown separately. Excludes fees, taxes, FX and other commitments. Not financial
                    advice.
                  </p>
                </>
              ) : component === "accounts" ? (
                <>
                  <h2>Balances</h2>
                  {v2Accounts.accounts.map((a) => (
                    <div className="v2-balance-row" key={a.account_id}>
                      <span>{a.label}</span>
                      <strong>{displayMoney(a.balance, a.currency)}</strong>
                    </div>
                  ))}
                </>
              ) : component === "recipient" ? (
                <>
                  <h2>Recipient</h2>
                  {edited.actions
                    .filter((a) => a.type === "transfer")
                    .map((a, i) => (
                      <p key={i}>{a.type === "transfer" ? a.to_payee_label : ""}</p>
                    ))}
                  <small>Mock recipient. Bank verification required for real transfers.</small>
                </>
              ) : (
                <>
                  <h2>Current holdings</h2>
                  {v2Accounts.holdings.map((h) => (
                    <p key={h.ticker}>
                      {h.ticker} · {h.quantity} shares
                    </p>
                  ))}
                </>
              )}
            </section>
          ))}
          {manifest.tools.includes("comparison") && (
            <section className="v2-context">
              <p className="eyebrow">ACCOUNT COMPARISON</p>
              <table>
                <caption>Balances by currency</caption>
                <thead>
                  <tr>
                    <th>Account</th>
                    <th>Balance</th>
                  </tr>
                </thead>
                <tbody>
                  {v2Accounts.accounts.map((a) => (
                    <tr key={a.account_id}>
                      <td>{a.label}</td>
                      <td>{displayMoney(a.balance, a.currency)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </aside>
      )}
    </div>
  );
}

function SimulationControl({
  actions,
  target,
  dispatch,
}: {
  actions: Action[];
  target: number;
  dispatch: (event: ConversationEvent) => void;
}) {
  return (
    <section className="v2-context v2-simulation">
      <p className="eyebrow">ADJUST AMOUNT</p>

      {actions.map((a, index) => {
        if (index !== target) return null;
        if (a.type === "stock_order")
          return (
            <label key={index}>
              Apple shares{" "}
              <input
                aria-label={`Simulate shares for action ${index + 1}`}
                type="range"
                min="1"
                max={Math.max(200, a.quantity)}
                step="1"
                value={a.quantity}
                onChange={(e) => dispatch({ type: "edit", index, field: "quantity", value: e.target.value })}
              />
            </label>
          );
        const cents = BigInt(a.amount.replace(".", ""));
        if (cents > 10000000n) return null;
        return (
          <label key={index}>
            {a.type === "transfer" ? "Transfer" : "Bill payment"} ({a.currency})
            <input
              aria-label={`Simulate amount for action ${index + 1}`}
              type="range"
              min="100"
              max={Math.max(320000, Number(cents))}
              step="1"
              value={Number(cents)}
              onChange={(e) => {
                const c = BigInt(e.target.value);
                dispatch({
                  type: "edit",
                  index,
                  field: "amount",
                  value: `${c / 100n}.${(c % 100n).toString().padStart(2, "0")}`,
                });
              }}
            />
          </label>
        );
      })}
    </section>
  );
}
