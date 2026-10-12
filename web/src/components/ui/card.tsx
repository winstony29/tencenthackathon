// Adapted from shadcn/ui card (MIT); see THIRD_PARTY_LICENSES.txt.
import type { ComponentProps } from "react";

export function Card({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="card" className={`ui-card ${className ?? ""}`} {...props} />;
}
export function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="card-header" className={`ui-card-header ${className ?? ""}`} {...props} />;
}
export function CardTitle({ className, ...props }: ComponentProps<"h2">) {
  return <h2 data-slot="card-title" className={`ui-card-title ${className ?? ""}`} {...props} />;
}
export function CardContent({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="card-content" className={`ui-card-content ${className ?? ""}`} {...props} />;
}
