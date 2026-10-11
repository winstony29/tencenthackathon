import { actionSchema, draftSchema, type Action, type Accounts, type Draft } from "./contracts";
import { exampleAccounts, exampleDraft } from "./examples";

// Explicit mock banking records; never sent to validation, signing or execution.
export const v2Accounts: Accounts = {
  ...exampleAccounts,
  payees: [
    ...exampleAccounts.payees,
    { payee_id: "pay_8892", name: "John Smith", label: "John Smith ...8892" },
  ],
};
export type PartialTransfer = {
  kind: "transfer";
  amount: string | null;
  recipient: string | null;
  source: string | null;
  candidates: string[];
};
export type ParsedCommand = {
  draft?: Draft;
  partial?: PartialTransfer;
  mode: "transaction" | "decision";
  error?: string;
};
function parseAmount(text: string): string | null {
  const matches = [...text.matchAll(/(?:\$|\bSGD\s*|\bUSD\s*)\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?![\d.])/gi)];
  const plain = text.match(/(?:amount(?: to)?|make it|send|transfer)\s+(\d+(?:\.\d{1,2})?)(?![\d.])/i);
  if (matches.length > 1) return null;
  const value = matches[0]?.[1] ?? plain?.[1];
  if (!value) return null;
  const [whole, fraction = ""] = value.replaceAll(",", "").split(".");
  return `${whole}.${fraction.padEnd(2, "0")}`;
}
function invalidNumbers(text: string): boolean {
  const money = [...text.matchAll(/(?:\$|\b(?:SGD|USD)\b)\s*(\S+)/gi)];
  return (
    money.length > 1 ||
    money.some((m) => !/^\d+(?:,\d{3})*(?:\.\d{1,2})?[,.!?]?$/.test(m[1])) ||
    /(?:^|\s|\$)[+-]\s*\d/.test(text) ||
    /\d+\.\d+\s+(?:apple\s+)?shares?\b/i.test(text)
  );
}
export function refineTransfer(partial: PartialTransfer, text: string): PartialTransfer | null {
  if (/\bUSD\b/i.test(text) || invalidNumbers(text)) return null;
  const named = v2Accounts.payees.filter((p) => text.toLowerCase().includes(p.name.toLowerCase()));
  const amount = parseAmount(text);
  if (named.length > 1 || (!amount && named.length !== 1)) return null;
  return { ...partial, amount: amount ?? partial.amount, recipient: named[0]?.payee_id ?? partial.recipient };
}
export function makeDraft(actions: Action[], transcript: string, revision: number): Draft {
  return draftSchema.parse({
    draft_id: `d_v2_demo_${revision}`,
    user_id: "u_demo",
    session_id: "s_v2_demo",
    transcript,
    created_at: "2026-10-10T09:30:00+08:00",
    actions,
    evidence: [],
  });
}
export function completeTransfer(
  partial: PartialTransfer,
  transcript: string,
  revision: number,
): Draft | null {
  if (!partial.amount || !partial.recipient || !partial.source) return null;
  const payee = v2Accounts.payees.find((p) => p.payee_id === partial.recipient);
  const account = v2Accounts.accounts.find((a) => a.account_id === partial.source);
  if (!payee || !account || account.currency !== "SGD" || /^0+\.00$/.test(partial.amount)) return null;
  const action = actionSchema.safeParse({
    type: "transfer",
    amount: partial.amount,
    currency: "SGD",
    to_payee_id: payee.payee_id,
    to_payee_label: payee.label,
    from_account_id: account.account_id,
    from_account_label: account.label,
  });
  return action.success ? makeDraft([action.data], transcript, revision) : null;
}
export function parseDemoCommand(text: string, revision: number): ParsedCommand {
  const mode = /\b(afford|affordability|remaining|enough|simulate)\b/i.test(text)
    ? "decision"
    : "transaction";
  if (invalidNumbers(text))
    return {
      mode,
      error:
        "Use one positive numeric amount with at most two decimals, and whole share quantities. Ambiguous values are not applied.",
    };
  if (/\bUSD\b/i.test(text) && /\b(transfer|send)\b/i.test(text))
    return { mode, error: "This transfer demo supports SGD only. Choose a supported currency explicitly." };
  if (/\b(transfer|send)\b/i.test(text)) {
    if (/\b(bill|shares?|stock)\b/i.test(text))
      return {
        mode,
        error:
          "This demo cannot safely parse a transfer mixed with other tasks. Start with the transfer, or use the bill-and-shares example.",
      };
    const named = v2Accounts.payees.filter((p) =>
      new RegExp(`\\b${p.name.split(" ")[0]}\\b`, "i").test(text),
    );
    const exact = named.filter((p) => text.toLowerCase().includes(p.name.toLowerCase()));
    const account = v2Accounts.accounts.filter((a) => a.currency === "SGD" && a.type === "savings");
    const partial: PartialTransfer = {
      kind: "transfer",
      amount: parseAmount(text),
      recipient: exact.length === 1 ? exact[0].payee_id : named.length === 1 ? named[0].payee_id : null,
      source: account.length === 1 ? account[0].account_id : null,
      candidates: named.map((p) => p.payee_id),
    };
    if (!partial.candidates.length)
      return { mode, error: "No listed recipient matches. Say a name from the mock recipient list." };
    const draft = completeTransfer(partial, text, revision);
    return draft ? { draft, mode } : { partial, mode };
  }
  const bill = /\b(bill|credit card)\b/i.test(text);
  const stock = /\b(shares?|stock|invest|apple|AAPL)\b/i.test(text);
  if (!bill && !stock)
    return {
      mode,
      error:
        "Try a transfer, a credit-card bill or an Apple share order. This offline demo supports numeric amounts only.",
    };
  if (stock && !/\b(apple|AAPL)\b/i.test(text))
    return { mode, error: "Name a stock in the mock catalogue: Apple (AAPL)." };
  const quantity = text.match(/\b(\d+)\s+(?:apple\s+)?shares?\b/i);
  if (stock && !quantity)
    return { mode, error: "How many Apple shares? Include a whole share quantity in your instruction." };
  const actions: Action[] = [];
  if (bill) {
    const action = exampleDraft(1).actions[0];
    const amount = parseAmount(text);
    actions.push(action.type === "bill_payment" && amount ? { ...action, amount } : action);
  }
  if (stock) {
    const action = exampleDraft(2).actions[0];
    if (action.type !== "stock_order") throw new Error("Invalid stock fixture");
    const parsed = actionSchema.safeParse({
      ...action,
      quantity: Number(quantity![1]),
      side: /\bsell\b/i.test(text) ? "sell" : "buy",
    });
    if (!parsed.success || !Number.isSafeInteger(Number(quantity![1])))
      return { mode, error: "Enter a positive whole share quantity." };
    actions.push(parsed.data);
  }
  return { draft: makeDraft(actions, text, revision), mode };
}
export function amendment(
  text: string,
  draft: Draft,
): { index?: number; fields?: Record<string, string>; mode?: "decision"; error?: string } {
  if (invalidNumbers(text))
    return { error: "That numeric change is invalid or ambiguous. Your current task is preserved." };
  if (/\bUSD\b/i.test(text))
    return { error: "Currency changes need a new validated task. Your current task is preserved." };
  if (
    /\b(afford|affordability|remaining|enough|simulate)\b/i.test(text) &&
    !/\b(make|change|instead)\b/i.test(text)
  )
    return { mode: "decision" };
  let candidates = draft.actions.map((action, index) => ({ action, index }));
  if (/\b(bill|credit card)\b/i.test(text))
    candidates = candidates.filter((c) => c.action.type === "bill_payment");
  else if (/\b(share|shares|stock|apple|AAPL)\b/i.test(text))
    candidates = candidates.filter((c) => c.action.type === "stock_order");
  else if (/\b(transfer|send|recipient)\b/i.test(text))
    candidates = candidates.filter((c) => c.action.type === "transfer");
  if (candidates.length !== 1)
    return { error: "Which action should change? Mention the transfer, bill or shares." };
  const { action, index } = candidates[0];
  const fields: Record<string, string> = {};
  const amount = parseAmount(text);
  if (amount && action.type !== "stock_order") fields.amount = amount;
  const quantity = text.match(/\b(\d+)\s+(?:apple\s+)?shares?\b/i);
  if (quantity && action.type === "stock_order") fields.quantity = quantity[1];
  if (/\bsavings\b/i.test(text)) {
    const accounts = v2Accounts.accounts.filter(
      (a) => a.type === "savings" && a.currency === action.currency,
    );
    if (accounts.length !== 1)
      return {
        error:
          "No unique savings account exists in this transaction currency. Choose an account in the form.",
      };
    fields.from_account_id = accounts[0].account_id;
  }
  const named = v2Accounts.payees.filter((p) => text.toLowerCase().includes(p.name.toLowerCase()));
  if (action.type === "transfer" && named.length === 1) fields.to_payee_id = named[0].payee_id;
  if (!Object.keys(fields).length)
    return {
      error:
        "I need a numeric amount, a share quantity or a listed account/recipient. Your current task is preserved.",
    };
  return { index, fields };
}
