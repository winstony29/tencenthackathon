import type { ReactNode } from "react";
import { displayMoney } from "@/lib/money";

export function SectionHeading({
  eyebrow,
  title,
  aside,
}: {
  eyebrow: string;
  title: string;
  aside?: ReactNode;
}) {
  return (
    <header className="v3-section-heading">
      <div>
        <p className="v3-kicker">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      {aside}
    </header>
  );
}
export function FinancialMetric({
  label,
  value,
  currency,
  note,
}: {
  label: string;
  value: string | null;
  currency: string;
  note?: string;
}) {
  return (
    <div className="v3-metric">
      <span>{label.replace(/ for task \d+$/, "")}</span>
      <strong className={value?.startsWith("-") ? "v3-negative" : ""}>
        {value === null ? "Unavailable" : displayMoney(value, currency)}
      </strong>
      {note && <small>{note}</small>}
    </div>
  );
}
export function AmountInput({
  label,
  value,
  currency,
  onChange,
  quantity = false,
  error,
}: {
  label: string;
  value: string;
  currency: string;
  onChange: (value: string) => void;
  quantity?: boolean;
  error?: string;
}) {
  return (
    <label className="v3-amount">
      <span>{label.replace(/ for task \d+$/, "")}</span>
      <div>
        <b>{quantity ? "#" : currency}</b>
        <input
          aria-label={label}
          aria-invalid={!!error}
          inputMode={quantity ? "numeric" : "decimal"}
          autoComplete="off"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
        />
      </div>
    </label>
  );
}
export function SelectField({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <label className="v3-select">
      <span>{label.replace(/ for task \d+$/, "")}</span>
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </label>
  );
}
export function InlineNotice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return (
    <p className={`v3-note${error ? " v3-negative" : ""}`} role={error ? "alert" : undefined}>
      {children}
    </p>
  );
}
