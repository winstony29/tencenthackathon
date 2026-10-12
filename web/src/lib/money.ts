import type { Action } from "./contracts";

export function estimatedTotal(price: string, quantity: number): string {
  if (!/^\d+\.\d{2}$/.test(price) || !Number.isSafeInteger(quantity) || quantity < 0)
    throw new Error("Invalid stock estimate");
  const cents = BigInt(price.replace(".", "")) * BigInt(quantity);
  return `${cents / 100n}.${(cents % 100n).toString().padStart(2, "0")}`;
}

export function displayMoney(value: string, currency: string): string {
  const [whole, fraction] = value.split(".");
  return `${currency} ${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction}`;
}

// Currency drawn from the action's from-account; a conversion spends its sell currency.
export function fromCurrency(action: Action): string {
  return action.type === "fx_conversion" ? action.sell_currency : action.currency;
}

// Fixed money leaving the from-account; stock orders depend on a quote, so they return null.
export function outgoing(action: Action): { amount: string; currency: string } | null {
  if (action.type === "fx_conversion") return { amount: action.sell_amount, currency: action.sell_currency };
  return action.type === "stock_order" ? null : { amount: action.amount, currency: action.currency };
}
