import { useId, type ReactNode } from "react";
import { displayMoney } from "@/lib/money";

export const activityLabel = (value: string) => value.replace(/^(?:Fictional|Synthetic) (?:scheduled )?/i, "");

export function Heading({ eyebrow, title, aside }: { eyebrow?: string; title: string; aside?: ReactNode }) {
  return (
    <header className="v4-section-heading">
      <div>
        {eyebrow && <p className="v4-eyebrow">{eyebrow}</p>}
        <h2>{title}</h2>
      </div>
      {aside}
    </header>
  );
}
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  return (
    <p className={`v4-note${error ? " v4-error" : ""}`} role={error ? "alert" : undefined}>
      {children}
    </p>
  );
}
export function Amount({
  label,
  value,
  currency,
  quantity = false,
  onChange,
  error,
}: {
  label: string;
  value: string;
  currency: string;
  quantity?: boolean;
  onChange: (value: string) => void;
  error?: string;
}) {
  const id = useId();
  return (
    <div className="v4-amount">
      <label htmlFor={id}>{label.replace(/ for task \d+$/, "")}</label>
      <div>
        <span aria-hidden="true">{quantity ? "#" : currency}</span>
        <input
          id={id}
          data-focus-key={label}
          aria-label={label}
          inputMode={quantity ? "numeric" : "decimal"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={!!error}
          aria-describedby={error ? `${id}-error` : undefined}
          autoComplete="off"
          spellCheck={false}
        />
      </div>
      {error && (
        <p id={`${id}-error`} className="v4-error">
          {error}
        </p>
      )}
    </div>
  );
}
export function Select({
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
  const id = useId();
  return (
    <label className="v4-select" htmlFor={id}>
      <span>{label.replace(/ for task \d+$/, "")}</span>
      <select
        id={id}
        data-focus-key={label}
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {children}
      </select>
    </label>
  );
}
export function Metric({
  label,
  value,
  currency,
}: {
  label: string;
  value: string | null;
  currency: string;
}) {
  return (
    <div className="v4-metric">
      <span>{label}</span>
      <strong className={value?.startsWith("-") ? "v4-error" : ""}>
        {value === null ? "Unavailable" : displayMoney(value, currency)}
      </strong>
    </div>
  );
}
