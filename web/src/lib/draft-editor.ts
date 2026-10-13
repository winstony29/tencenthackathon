import { actionSchema, type Accounts, type Action, type Draft, type Quote } from "./contracts";
import { estimatedTotal, fromCurrency } from "./money";
export type DraftEdits = Record<number, Record<string, string>>;
export function updateEdit(edits: DraftEdits, index: number, field: string, value: string): DraftEdits {
  return { ...edits, [index]: { ...edits[index], [field]: value } };
}
export function editedActions(
  draft: Draft,
  edits: DraftEdits,
  accounts: Accounts | null,
): { actions: Action[]; errors: Record<number, string> } {
  const errors: Record<number, string> = {};
  const actions = draft.actions.map((original, index) => {
    const edit = edits[index] ?? {};
    const value = { ...original };
    const allowed =
      original.type === "stock_order"
        ? ["quantity", "order_type", "limit_price", "from_account_id"]
        : original.type === "transfer"
          ? ["amount", "to_payee_id", "from_account_id"]
          : original.type === "bill_payment"
            ? ["amount", "from_account_id"]
            : original.type === "account_transfer"
              ? ["amount"] // both ends are the user's own accounts; change them by request
              : []; // a conversion is derived by code from the other actions
    if (
      Object.keys(edit).some((field) => !allowed.includes(field)) ||
      (edit.order_type !== undefined && !["market", "limit"].includes(edit.order_type))
    )
      errors[index] = "This field is not editable. Clarify your request instead.";
    if ("amount" in value && edit.amount !== undefined) value.amount = edit.amount;
    if (value.type === "stock_order") {
      if (edit.quantity !== undefined)
        value.quantity = /^\d+$/.test(edit.quantity) ? Number(edit.quantity) : NaN;
      if (edit.order_type === "market" || edit.order_type === "limit") {
        value.order_type = edit.order_type;
        value.limit_price = edit.order_type === "market" ? null : (edit.limit_price ?? value.limit_price);
      }
      if (edit.limit_price !== undefined && value.order_type === "limit")
        value.limit_price = edit.limit_price;
    }
    if (edit.from_account_id !== undefined) {
      const account = accounts?.accounts.find(
        (a) => a.account_id === edit.from_account_id && a.currency === fromCurrency(value),
      );
      if (!account) errors[index] = "Choose an available account in the transaction currency.";
      else {
        value.from_account_id = account.account_id;
        value.from_account_label = account.label;
      }
    }
    if (value.type === "transfer" && edit.to_payee_id !== undefined) {
      const payee = accounts?.payees.find((a) => a.payee_id === edit.to_payee_id);
      if (!payee) errors[index] = "Choose a listed recipient, or clarify your request.";
      else {
        value.to_payee_id = payee.payee_id;
        value.to_payee_label = payee.label;
      }
    }
    const parsed = actionSchema.safeParse(value);
    if (
      !parsed.success ||
      ("amount" in value && /^0+\.00$/.test(value.amount)) ||
      (value.type === "stock_order" &&
        (!Number.isSafeInteger(value.quantity) ||
          (value.order_type === "limit" && /^0+\.00$/.test(value.limit_price ?? ""))))
    )
      errors[index] =
        "Enter a positive amount with two decimals, a whole share quantity, and a limit price when required.";
    return parsed.success ? parsed.data : original;
  });
  return { actions, errors };
}
function cents(value: string): bigint {
  return BigInt(value.replace(".", ""));
}
function decimal(value: bigint): string {
  const absolute = value < 0n ? -value : value;
  return `${value < 0n ? "-" : ""}${absolute / 100n}.${(absolute % 100n).toString().padStart(2, "0")}`;
}
export function projectedBalances(actions: Action[], accounts: Accounts, quotes: Record<string, Quote>) {
  return accounts.accounts
    .filter((account) =>
      actions.some(
        (a) =>
          a.from_account_id === account.account_id ||
          ((a.type === "fx_conversion" || a.type === "account_transfer") &&
            a.to_account_id === account.account_id),
      ),
    )
    .map((account) => {
      let change = 0n;
      let complete = true;
      for (const action of actions) {
        if (action.type === "fx_conversion" && action.to_account_id === account.account_id) {
          if (action.buy_currency !== account.currency) complete = false;
          else change += cents(action.buy_amount);
        }
        if (action.type === "account_transfer" && action.to_account_id === account.account_id) {
          if (action.currency !== account.currency) complete = false;
          else change += cents(action.amount);
        }
        if (action.from_account_id !== account.account_id) continue;
        if (fromCurrency(action) !== account.currency) {
          complete = false;
          continue;
        }
        if (action.type === "fx_conversion") change -= cents(action.sell_amount);
        else if (action.type !== "stock_order") change -= cents(action.amount);
        else {
          const quote = quotes[action.ticker];
          const price =
            action.order_type === "limit"
              ? action.limit_price
              : quote?.ticker === action.ticker && quote.currency === action.currency
                ? quote.price
                : null;
          if (!price) {
            complete = false;
            continue;
          }
          const total = cents(estimatedTotal(price, action.quantity));
          change += action.side === "sell" ? total : -total;
        }
      }
      return {
        ...account,
        projected: complete ? decimal(cents(account.balance) + change) : null,
        change: decimal(change),
      };
    });
}
