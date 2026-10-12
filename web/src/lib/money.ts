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
