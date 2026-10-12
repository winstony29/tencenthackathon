import type { ComponentProps } from "react";
// Adapted from shadcn/ui textarea (MIT); see THIRD_PARTY_LICENSES.txt.
export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea data-slot="textarea" className={`ui-textarea ${className ?? ""}`} {...props} />;
}
