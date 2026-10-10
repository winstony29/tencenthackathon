"use client";
import { Button } from "@/components/ui/button";

import { useEffect, useState } from "react";
import { accountsSchema, quoteSchema, type Accounts, type Draft, type Quote } from "@/lib/contracts";
import { request } from "@/lib/api";
import { exampleAccounts, exampleQuote } from "@/lib/examples";
import { displayMoney } from "@/lib/money";

export function ContextPanel({
  draft,
  demo,
  onQuotes,
}: {
  draft: Draft;
  demo: boolean;
  onQuotes: (quotes: Record<string, Quote>) => void;
}) {
  const [accounts, setAccounts] = useState<Accounts | null>(null);
  const [accountError, setAccountError] = useState("");
  const [quoteError, setQuoteError] = useState("");
  const error = [accountError, quoteError].filter(Boolean).join(" ");
  const [refresh, setRefresh] = useState(0);
  const tickers = draft.actions.flatMap((a) => (a.type === "stock_order" ? [a.ticker] : [])).join(",");
  useEffect(() => {
    const controller = new AbortController();
    const quotes: Record<string, Quote> = {};
    let refreshingQuotes = false;
    async function load() {
      try {
        const data = demo
          ? exampleAccounts
          : await request(`/accounts?user_id=${encodeURIComponent(draft.user_id)}`, accountsSchema, {
              signal: controller.signal,
            });
        if (controller.signal.aborted) return;
        setAccounts(data);
        setAccountError("");
      } catch (e) {
        if (!controller.signal.aborted)
          setAccountError(e instanceof Error ? e.message : "Could not load accounts.");
      }
    }
    async function loadQuotes() {
      if (refreshingQuotes) return;
      refreshingQuotes = true;
      try {
        await Promise.all(
          [...new Set(tickers.split(",").filter(Boolean))].map(async (ticker) => {
            const quote = demo
              ? exampleQuote
              : await request(`/quote?ticker=${encodeURIComponent(ticker)}`, quoteSchema, {
                  signal: controller.signal,
                });
            if (
              quote.ticker !== ticker ||
              !draft.actions.some(
                (a) => a.type === "stock_order" && a.ticker === ticker && a.currency === quote.currency,
              )
            )
              throw new Error("The quote does not match this order.");
            quotes[ticker] = quote;
          }),
        );
        if (!controller.signal.aborted) {
          onQuotes({ ...quotes });
          setQuoteError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          onQuotes({});
          setQuoteError(e instanceof Error ? e.message : "Could not refresh quotes.");
        }
      } finally {
        refreshingQuotes = false;
      }
    }
    void load();
    void loadQuotes();
    const interval = tickers && !demo ? setInterval(loadQuotes, 10000) : undefined;
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [draft, demo, tickers, refresh, onQuotes]);
  const accountIds = new Set(draft.actions.map((a) => a.from_account_id));
  return (
    <aside className="context-panel" aria-label="Related banking information">
      <span className="eyebrow">ALONGSIDE YOUR TASK</span>
      <h2>A little more context.</h2>
      <p>Useful details, right where you need them.</p>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <details open>
        <summary>Your paying accounts</summary>
        {accounts ? (
          accounts.accounts
            .filter((a) => accountIds.has(a.account_id))
            .map((a) => (
              <div className="context-row" key={a.account_id}>
                <span>
                  {a.label}
                  <small>Available balance · {demo ? "example" : "stub"}</small>
                </span>
                <strong>{displayMoney(a.balance, a.currency)}</strong>
              </div>
            ))
        ) : (
          <p className="fine-print">
            {accountError ? "Balance details are unavailable." : "Loading balances…"}
          </p>
        )}
        {accounts && !accounts.accounts.some((a) => accountIds.has(a.account_id)) && (
          <p className="fine-print">This account’s balance isn’t available in the stub yet.</p>
        )}
      </details>
      {tickers && (
        <details open>
          <summary>Your holdings</summary>
          {accounts?.holdings
            .filter((h) => tickers.split(",").includes(h.ticker))
            .map((h) => (
              <div className="context-row" key={h.ticker}>
                <span>
                  {h.ticker}
                  <small>Current position</small>
                </span>
                <strong>{h.quantity} shares</strong>
              </div>
            ))}
          {accounts && !accounts.holdings.some((h) => tickers.split(",").includes(h.ticker)) && (
            <p className="fine-print">No holdings in these stocks.</p>
          )}
        </details>
      )}
      <Button variant="link" className="text-button" onClick={() => setRefresh((n) => n + 1)}>
        Refresh details <span aria-hidden="true">↻</span>
      </Button>
      <div className="context-note">
        <span aria-hidden="true">◎</span>
        <p>These panels give you context. They don’t add anything to your transaction.</p>
      </div>
    </aside>
  );
}

export function WelcomePanel() {
  return (
    <aside className="welcome-panel">
      <span className="eyebrow">BUILT AROUND YOUR INTENT</span>
      <h2>
        Less navigating.
        <br />
        More doing.
      </h2>
      <p>Your task page adapts to what you ask for.</p>
      <ol>
        <li>
          <span>01</span>
          <div>
            <strong>Tell us what you need</strong>
            <p>One request, in your own words.</p>
          </div>
        </li>
        <li>
          <span>02</span>
          <div>
            <strong>Make sure it’s right</strong>
            <p>Clarify, review, and correct.</p>
          </div>
        </li>
        <li>
          <span>03</span>
          <div>
            <strong>Approve before anything moves</strong>
            <p>You stay in control of each action.</p>
          </div>
        </li>
      </ol>
      <div className="context-note">
        <span aria-hidden="true">◎</span>
        <p>Only a signed draft can reach the mock bank once the gateway is connected.</p>
      </div>
    </aside>
  );
}
