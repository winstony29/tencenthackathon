import type { Action, Accounts, Draft, Quote } from "./contracts";

const transfer: Action = {
  type: "transfer",
  from_account_id: "acc_1234",
  from_account_label: "Savings …1234",
  to_payee_id: "pay_4521",
  to_payee_label: "John Doe …4521",
  amount: "500.00",
  currency: "SGD",
};
const bill: Action = {
  type: "bill_payment",
  from_account_id: "acc_1234",
  from_account_label: "Savings …1234",
  biller_id: "bil_0001",
  biller_label: "DBS Credit Card …9012",
  bill_reference: "4120-XXXX-XXXX-9012",
  amount: "1240.00",
  currency: "SGD",
};
const stock: Action = {
  type: "stock_order",
  from_account_id: "acc_7788",
  from_account_label: "Invest (USD) …7788",
  side: "buy",
  ticker: "AAPL",
  company_name: "Apple Inc.",
  quantity: 100,
  order_type: "market",
  limit_price: null,
  currency: "USD",
};
export const examples = [
  { label: "Send money", text: "Transfer $500 to John.", actions: [transfer] },
  { label: "Pay a bill", text: "Pay my credit card bill", actions: [bill] },
  { label: "Buy shares", text: "Buy 100 shares of Apple", actions: [stock] },
  { label: "Two tasks", text: "Pay my credit card bill and buy 100 shares of Apple", actions: [bill, stock] },
  {
    label: "Can I afford it?",
    text: "Can I afford to buy 100 Apple shares after paying my credit card bill?",
    actions: [bill, stock],
    mode: "decision" as const,
  },
];
export function exampleDraft(index: number): Draft {
  const example = examples[index];
  return {
    draft_id: `d_example_${index}`,
    user_id: "u_demo",
    session_id: "s_example",
    transcript: example.text,
    created_at: "2026-10-10T09:30:00+08:00",
    actions: example.actions,
    evidence: [],
  };
}
export const exampleAccounts: Accounts = {
  accounts: [
    { account_id: "acc_1234", label: "Savings …1234", type: "savings", currency: "SGD", balance: "3200.00" },
    {
      account_id: "acc_7788",
      label: "Invest (USD) …7788",
      type: "investment",
      currency: "USD",
      balance: "30000.00",
    },
  ],
  payees: [{ payee_id: "pay_4521", name: "John Doe", label: "John Doe …4521" }],
  billers: [
    { biller_id: "bil_0001", name: "DBS Credit Card", label: "DBS Credit Card …9012", amount_due: "1240.00" },
  ],
  holdings: [{ ticker: "AAPL", quantity: 10 }],
};
export const exampleQuote: Quote = {
  ticker: "AAPL",
  company_name: "Apple Inc.",
  price: "227.50",
  currency: "USD",
  as_of: "2026-10-10T09:30:00+08:00",
};

// Complete mock Winston handoffs. Objectives and ambiguity resolution belong upstream.
export const winstonFixtures = [
  {
    id: "transfer",
    label: "Transfer: ready to edit",
    draft: exampleDraft(0),
    partial: null,
    mode: "transaction",
    objective: "prepare",
    recipientStatus: "listed",
  },
  {
    id: "transfer-affordability",
    label: "Same transfer: affordability",
    draft: exampleDraft(0),
    partial: null,
    mode: "decision",
    objective: "assess_affordability",
    recipientStatus: "listed",
  },
  {
    id: "transfer-recipient",
    label: "Same transfer: check recipient",
    draft: exampleDraft(0),
    partial: null,
    mode: "transaction",
    objective: "check_recipient",
    recipientStatus: "listed",
  },
  {
    id: "transfer-amended",
    label: "Same transfer: updated to $300",
    draft: { ...exampleDraft(0), actions: [{ ...transfer, amount: "300.00" }] as Draft["actions"] },
    partial: null,
    mode: "transaction",
    objective: "prepare",
    recipientStatus: "listed",
  },
  {
    id: "multi",
    label: "Bill and Apple shares",
    draft: exampleDraft(3),
    partial: null,
    mode: "transaction",
    objective: "prepare",
    recipientStatus: "not_applicable",
  },
  {
    id: "decision",
    label: "Affordability for both",
    draft: exampleDraft(3),
    partial: null,
    mode: "decision",
    objective: "assess_affordability",
    recipientStatus: "not_applicable",
  },
] as const;
