"use client";
// Adapted from coss.com apps/ui button (MIT); see THIRD_PARTY_LICENSES.txt.
import { mergeProps } from "@base-ui/react/merge-props";
import { useRender } from "@base-ui/react/use-render";
import { LoaderCircle } from "lucide-react";

type ButtonProps = useRender.ComponentProps<"button"> & {
  variant?: "default" | "outline" | "ghost" | "link";
  size?: "default" | "sm";
  loading?: boolean;
};
export function Button({
  className,
  variant = "default",
  size = "default",
  loading = false,
  render,
  children,
  ...props
}: ButtonProps) {
  const defaultProps = {
    type: "button" as const,
    className: `ui-button ${className ?? ""}`,
    "data-slot": "button",
    "data-variant": variant,
    "data-size": size,
    "data-loading": loading || undefined,
    disabled: loading || props.disabled,
    children: (
      <>
        <span className="ui-button-content">{children}</span>
        {loading && <LoaderCircle className="button-spinner" size={18} aria-hidden="true" />}
      </>
    ),
  };
  return useRender({
    defaultTagName: "button",
    render,
    props: mergeProps<"button">(defaultProps, props, { disabled: loading || props.disabled }),
  });
}
