"use client";
// Adapted from coss.com apps/ui checkbox (MIT); see THIRD_PARTY_LICENSES.txt.
import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox";
import { Check, Minus } from "lucide-react";

type Props = Omit<CheckboxPrimitive.Root.Props, "className"> & { className?: string };
export function Checkbox({ className, ...props }: Props) {
  return (
    <CheckboxPrimitive.Root data-slot="checkbox" className={`ui-checkbox ${className ?? ""}`} {...props}>
      <CheckboxPrimitive.Indicator
        data-slot="checkbox-indicator"
        render={(indicatorProps, state) => (
          <span {...indicatorProps}>
            {state.indeterminate ? (
              <Minus size={14} aria-hidden="true" />
            ) : (
              <Check size={14} aria-hidden="true" />
            )}
          </span>
        )}
      />
    </CheckboxPrimitive.Root>
  );
}
