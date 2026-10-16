"use client";
import { motion, useReducedMotion } from "motion/react";

export function V5Orb({
  active = false,
  busy = false,
  listening = false,
  level = 0,
  onClick,
}: {
  active?: boolean;
  busy?: boolean;
  listening?: boolean;
  level?: number; // microphone RMS while listening, 0–1
  onClick?: () => void;
}) {
  const reduced = useReducedMotion();
  const scale = listening && !reduced ? 1 + Math.min(level * 4, 0.3) : 1;
  return (
    <motion.button
      layoutId={reduced ? undefined : "v5-surface"}
      className={`v5-orb ${active ? "is-active" : ""} ${busy ? "is-busy" : ""} ${listening ? "is-listening" : ""}`}
      style={{ borderRadius: "50%" }}
      animate={{ scale }}
      transition={{ duration: reduced ? 0 : listening ? 0.08 : 0.45, ease: [0.22, 1, 0.36, 1] }}
      onClick={onClick}
      disabled={busy}
      aria-label={
        busy ? "Working on your request" : listening ? "Listening, tap to stop" : "Speak your request"
      }
      aria-pressed={listening}
    >
      <span className="v5-orb-light" aria-hidden="true" />
      <span className="v5-orb-swirl" aria-hidden="true" />
    </motion.button>
  );
}
