"use client";
import { motion, useReducedMotion } from "motion/react";

export function V5Orb({
  active = false,
  busy = false,
  onClick,
}: {
  active?: boolean;
  busy?: boolean;
  onClick?: () => void;
}) {
  const reduced = useReducedMotion();
  return (
    <motion.button
      layoutId={reduced ? undefined : "v5-surface"}
      className={`v5-orb ${active ? "is-active" : ""} ${busy ? "is-busy" : ""}`}
      style={{ borderRadius: "50%" }}
      transition={{ duration: reduced ? 0 : 0.45, ease: [0.22, 1, 0.36, 1] }}
      onClick={onClick}
      disabled={busy}
      aria-label={
        busy ? "Preparing your request" : active ? "Close requests" : "Choose a banking request"
      }
      aria-expanded={busy ? undefined : active}
      aria-controls={busy ? undefined : "v5-requests"}
    >
      <span className="v5-orb-light" aria-hidden="true" />
      <span className="v5-orb-swirl" aria-hidden="true" />
    </motion.button>
  );
}
