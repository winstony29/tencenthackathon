"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowUpRight, RotateCcw, X } from "lucide-react";
import { V5Orb } from "@/components/v5-orb";
import { V5Cards } from "@/components/v5-cards";
import { compose, fallback, type Composition } from "@/lib/v4-composition";
import { contextFor } from "@/lib/v4-catalog";
import { demoHandoff } from "@/lib/v4-demo";
import { initialState, reducer, type Event, type Handoff, type State } from "@/lib/v4-state";
import "../v4/v4.css";
import "./v5.css";

type Phase = "idle" | "choose" | "clarify" | "composing" | "workspace" | "blocked";
const requests = [
  "Send $50 to John",
  "Pay my card bill and buy 100 Apple shares",
  "Can I afford to send $2,000 to John Doe?",
  "Try a blocked request",
];
const titles = {
  prepare: "Ready when you are.",
  assess_affordability: "See what stays with you.",
  simulate: "A little more. A little less.",
  review: "One last look.",
};

export default function V5() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [state, setState] = useState<State>(initialState);
  const [composition, setComposition] = useState<Composition | null>(null);
  const [heard, setHeard] = useState("");
  const [stage, setStage] = useState(0);
  const [bankLocked, setBankLocked] = useState(false);
  const current = useRef(state);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const reduced = useReducedMotion();
  const transition = { duration: reduced ? 0 : 0.45, ease: [0.22, 1, 0.36, 1] as const };

  useEffect(
    () => () => {
      generation.current++;
      controller.current?.abort();
    },
    [],
  );
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [phase, state.done]);

  function update(next: State) {
    current.current = next;
    setState(next);
  }
  function cancel() {
    generation.current++;
    controller.current?.abort();
  }
  function reset() {
    if (bankLocked) return;
    cancel();
    update(initialState);
    setComposition(null);
    setHeard("");
    setPhase("idle");
  }
  async function present(next: State) {
    cancel();
    if (!next.handoff || next.error) return;
    const version = generation.current;
    const abort = new AbortController();
    controller.current = abort;
    const ctx = contextFor(next, window.matchMedia("(max-width: 700px)").matches ? "mobile" : "desktop");
    setStage(0);
    setPhase("composing");
    let result: Composition;
    try {
      result = await compose(ctx, abort.signal, () => {
        if (version === generation.current) setStage((n) => n + 1);
      });
    } catch {
      result = fallback(ctx);
    }
    if (version !== generation.current) return;
    setComposition(result);
    setPhase("workspace");
  }
  // Replace this fixture producer with Winston's resolved handoff at integration.
  // The reducer validates every handoff before presentation receives it.
  function accept(handoff: Handoff, text: string) {
    handoff.draft.transcript = text;
    handoff.draft.created_at = new Date().toISOString();
    const next = reducer(initialState, { type: "handoff", value: handoff });
    update(next);
    setHeard(text);
    void present(next);
  }
  function pick(index: number) {
    setHeard(requests[index]);
    if (index === 0) {
      setPhase("clarify");
      return;
    }
    if (index === 3) {
      setPhase("blocked");
      return;
    }
    const handoff = demoHandoff(index === 1 ? "multi" : "transfer", index === 1 ? 0 : 1, initialState);
    if (index === 2 && handoff.draft.actions[0].type === "transfer")
      handoff.draft.actions[0].amount = "2000.00";
    accept(handoff, requests[index]);
  }
  function chooseRecipient(id: string) {
    const handoff = demoHandoff("transfer", 0, initialState);
    const payee = handoff.data.accounts.payees.find((p) => p.payee_id === id)!;
    const action = handoff.draft.actions[0];
    if (action.type === "transfer") {
      action.amount = "50.00";
      action.to_payee_id = payee.payee_id;
      action.to_payee_label = payee.label;
    }
    accept(handoff, `Send $50 to ${payee.name}`);
  }
  function dispatch(event: Event) {
    const next = reducer(current.current, event);
    update(next);
    if (event.type === "finish") cancel();
    if (["objective", "apply"].includes(event.type) && !next.error) void present(next);
  }
  const objective = state.handoff?.objective;
  const status =
    phase === "composing"
      ? stage < 2
        ? "Putting it together?"
        : "Almost ready?"
      : phase === "choose"
        ? "Choose a demo request"
        : phase === "clarify"
          ? "A quick clarification"
          : phase === "blocked"
            ? "Request stopped"
            : state.done
              ? "Demo complete"
              : phase === "workspace"
                ? "Workspace ready"
                : "Ready";

  return (
    <div className="v4-shell v5-shell" data-phase={phase}>
      <a className="v4-skip" href="#v5-content">
        Skip to content
      </a>
      <header className="v5-header">
        <Link href="/v5/" className="v5-brand" aria-label="JEV V5 home">
          jev<span> / PERSONAL</span>
        </Link>
        <span className="v5-status" role="status">
          <i data-busy={phase === "composing"} />
          {status}
        </span>
        <button className="v5-icon" aria-label="Start over" disabled={bankLocked} onClick={reset}>
          <RotateCcw size={18} />
        </button>
      </header>

      <div id="v5-content" className={`v5-stage ${phase === "workspace" ? "has-workspace" : ""}`}>
        <aside className="v5-intro">
          <p className="v5-eyebrow">BANKING, IN YOUR WORDS</p>
          <h1 ref={heading} tabIndex={-1}>
            {phase === "idle" || phase === "choose" ? (
              <>
                What can I<br />
                do for you?
              </>
            ) : state.done ? (
              "All checked."
            ) : phase === "workspace" && objective ? (
              titles[objective]
            ) : (
              "Let’s work through it."
            )}
          </h1>
          {heard ? (
            <div className="v5-heard">
              <span>Your request</span>
              <p>“{heard}”</p>
            </div>
          ) : (
            <p className="v5-lede">
              Your next move, made simple.
              <br />
              Tap the orb to begin.
            </p>
          )}

          {phase === "workspace" && !state.done && (
            <button className="v5-back" disabled={bankLocked} onClick={reset}>
              New request <ArrowUpRight size={15} />
            </button>
          )}
        </aside>

        <div className="v5-content">
          {(phase === "idle" || phase === "choose" || phase === "composing") && (
            <div className="v5-orb-area">
              <V5Orb
                active={phase === "choose"}
                busy={phase === "composing"}
                onClick={() => setPhase(phase === "choose" ? "idle" : "choose")}
              />
              <p>
                {phase === "composing"
                  ? status
                  : phase === "choose"
                    ? "What would you like to do?"
                    : "Tap to begin"}
              </p>
              {phase === "composing" && (
                <button className="v5-back" disabled={bankLocked} onClick={reset}>
                  Cancel
                </button>
              )}
            </div>
          )}
          <AnimatePresence>
            {phase === "choose" && (
              <motion.div
                id="v5-requests"
                className="v5-requests"
                initial={{ opacity: 0, y: reduced ? 0 : 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: reduced ? 0 : 0.2 }}
              >
                <p className="v5-eyebrow">DEMO · PICK WHAT YOU SAY</p>
                {requests.map((text, i) => (
                  <button key={text} onClick={() => pick(i)}>
                    <span>{text}</span>
                    <ArrowUpRight size={17} />
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>

          {(phase === "clarify" || phase === "blocked" || phase === "workspace") && (
            <motion.div
              layoutId={reduced ? undefined : "v5-surface"}
              transition={transition}
              style={{ borderRadius: 28 }}
              className="v5-surface"
            >
              {phase === "clarify" && (
                <>
                  <p className="v5-card-label">Winston clarification · demo</p>
                  <h2>Which John did you mean?</h2>
                  <div className="v5-recipients">
                    {demoHandoff("transfer", 0, initialState).data.accounts.payees.map((p) => (
                      <button key={p.payee_id} onClick={() => chooseRecipient(p.payee_id)}>
                        <span className="v5-avatar">
                          {p.name
                            .split(" ")
                            .map((s) => s[0])
                            .join("")}
                        </span>
                        <span>
                          <strong>{p.name}</strong>
                          <small>{p.label}</small>
                        </span>
                        <ArrowUpRight size={17} />
                      </button>
                    ))}
                  </div>
                  <button className="v5-quiet" onClick={reset}>
                    None of these · start over
                  </button>
                </>
              )}
              {phase === "blocked" && (
                <>
                  <div className="v5-result-icon">
                    <X size={26} />
                  </div>

                  <h2>Request stopped.</h2>
                  <p>Please rephrase your request. Nothing was sent.</p>
                  <button className="v4-button" onClick={reset}>
                    Start over
                  </button>
                </>
              )}
              {phase === "workspace" && composition && (
                <>
                  {state.error && <p role="alert">{state.error}</p>}
                  <V5Cards
                    state={state}
                    composition={composition}
                    onCancel={reset}
                    onLock={setBankLocked}
                    onAfford={() => dispatch({ type: "objective", value: "assess_affordability" })}
                  />
                </>
              )}
            </motion.div>
          )}
        </div>
      </div>
      <footer className="v5-footer">
        <span>Banking demo</span>
        <Link href="/v4/">
          Open V4 <ArrowUpRight size={12} />
        </Link>
      </footer>
    </div>
  );
}
