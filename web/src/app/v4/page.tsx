"use client";
import Link from "next/link";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { ArrowUpRight, Check, ChevronDown, CircleDot, RotateCcw } from "lucide-react";
import { V4Workspace } from "@/components/v4-workspace";
import { FinancialStatus } from "@/components/v4-context";
import { ReviewBar } from "@/components/v4-editors";
import { compose, fallback, type Composition } from "@/lib/v4-composition";
import { contextFor, type Context } from "@/lib/v4-catalog";
import { demoHandoff, journeys } from "@/lib/v4-demo";
import { initialState, reducer, type Event, type State } from "@/lib/v4-state";
import "./v4.css";

const start = () =>
  reducer(initialState, {
    type: "handoff",
    value: demoHandoff("transfer", 0, initialState),
  });
const titles = {
  prepare: "Make your transfer.",
  assess_affordability: "See what stays with you.",
  simulate: "Try a different amount.",
  review: "Check before you confirm.",
};
export default function V4() {
  const [state, setState] = useState<State>(start),
    current = useRef(state);
  const [journey, setJourney] = useState<keyof typeof journeys>("transfer"),
    [step, setStep] = useState(0);
  const [composition, setComposition] = useState<Composition>(() => fallback(contextFor(state, "desktop")));
  const [busy, setBusy] = useState(false),
    [calls, setCalls] = useState(0);
  const [cachedLayout, setCachedLayout] = useState(false);
  const [context, setContext] = useState<Context>(() => contextFor(state, "desktop"));
  const controller = useRef<AbortController | null>(null),
    generation = useRef(0),
    heading = useRef<HTMLHeadingElement>(null);
  const cache = useRef(new Map<string, Composition>());
  function update(next: State) {
    current.current = next;
    setState(next);
  }
  function cancel() {
    generation.current++;
    controller.current?.abort();
    setBusy(false);
  }
  function commit(result: Composition) {
    const active = document.activeElement;
    const input = active instanceof HTMLInputElement ? active : null;
    const key = active instanceof HTMLElement ? active.dataset.focusKey : undefined;
    const selection =
      input && input.type === "text" ? { start: input.selectionStart, end: input.selectionEnd } : null;
    setComposition(result);
    if (key)
      requestAnimationFrame(() => {
        // Restore only a focus lost to this commit, never a user's newer focus choice.
        if (document.activeElement !== document.body && document.activeElement !== active) return;
        const target = [...document.querySelectorAll<HTMLElement>("[data-focus-key]")].find(
          (n) => n.dataset.focusKey === key,
        );
        if (target) {
          target.focus({ preventScroll: true });
          if (target instanceof HTMLInputElement && selection)
            target.setSelectionRange(selection.start, selection.end);
        }
      });
  }
  async function present(next: State, force = false) {
    cancel();
    if (!next.handoff || next.done) return;
    const ctx = contextFor(next, window.matchMedia("(max-width: 700px)").matches ? "mobile" : "desktop");
    const key = JSON.stringify(ctx);
    setContext(ctx);
    const cached = cache.current.get(key);
    if (cached && !force) {
      setCachedLayout(true);
      commit(cached);
      return;
    }
    setCachedLayout(false);
    commit(cached ?? fallback(ctx));
    setBusy(true);
    const version = generation.current,
      abort = new AbortController();
    controller.current = abort;
    try {
      const result = await compose(ctx, abort.signal, () => {
        if (version === generation.current) setCalls((n) => n + 1);
      });
      if (version !== generation.current) return;
      if (result.source === "jev") cache.current.set(key, result);
      commit(result);
    } catch {
      if (version === generation.current) commit(fallback(ctx));
    } finally {
      if (version === generation.current) setBusy(false);
    }
  }
  const refresh = useEffectEvent(() => void present(current.current));
  const stop = useEffectEvent(() => {
    generation.current++;
    controller.current?.abort();
  });
  useEffect(() => {
    refresh();
    const viewport = window.matchMedia("(max-width: 700px)");
    const changed = () => refresh();
    viewport.addEventListener("change", changed);
    return () => {
      stop();
      viewport.removeEventListener("change", changed);
    };
  }, []);
  useEffect(() => {
    if (state.done || state.handoff?.objective === "review") heading.current?.focus();
  }, [state.done, state.handoff?.objective]);
  function turn(nextJourney: keyof typeof journeys, index: number) {
    const base = nextJourney === journey && index > 0 ? current.current : initialState;
    const next = reducer(base, {
      type: "handoff",
      value: demoHandoff(nextJourney, index, base),
    });
    setJourney(nextJourney);
    setStep(index);
    update(next);
    if (!next.error) void present(next);
  }
  function dispatch(event: Event) {
    const next = reducer(current.current, event);
    if (next === current.current) return;
    update(next);
    if (event.type === "finish") cancel();
    if (["objective", "apply"].includes(event.type) && !next.error) void present(next);
  }
  const objective = state.handoff!.objective;
  return (
    <div className="v4-shell">
      <a className="v4-skip" href="#v4-main">
        Skip to workspace
      </a>
      <nav className="v4-nav" aria-label="Workspace versions">
        <Link className="v4-brand" href="/v4/">
          <span aria-hidden="true">D</span>DCTA <i>/</i>
          <small>PERSONAL</small>
        </Link>
        <div>
          <span className="v4-demo">Demo · no real transactions</span>
          <Link href="/v3/">
            V3 <ArrowUpRight size={14} />
          </Link>
        </div>
      </nav>
      <div className="v4-stage">
        <aside className="v4-conversation" aria-label="Resolved Winston demo turns">
          <div className="v4-rail-heading">
            <span className="v4-eyebrow">YOUR CONVERSATION</span>
            <CircleDot size={16} />
          </div>
          <div className="v4-journeys" role="group" aria-label="Demo journey">
            <button aria-pressed={journey === "transfer"} onClick={() => turn("transfer", 0)}>
              Transfer
            </button>
            <button aria-pressed={journey === "multi"} onClick={() => turn("multi", 0)}>
              Pay & invest
            </button>
          </div>
          <ol className="v4-turns">
            {journeys[journey].map((t, i) => (
              <li key={t.text} data-active={step === i} data-past={step > i}>
                <button onClick={() => turn(journey, i)} aria-current={step === i ? "step" : undefined}>
                  <span className="v4-step">
                    {step > i ? <Check size={13} /> : String(i + 1).padStart(2, "0")}
                  </span>
                  <span>
                    <small>{t.label}</small>
                    <span>{t.text}</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <p className="v4-rail-note">
            Resolved demo tasks from Winston.
            <br />
            Your details stay in your control.
          </p>
        </aside>
        <div id="v4-main" className="v4-main" role="region" aria-label="Task workspace">
          <header className="v4-main-header">
            <div>
              <p className="v4-eyebrow">
                {objective === "simulate" ? "EXPLORE, THEN DECIDE" : "YOUR WORKSPACE"}
              </p>
              <h1 ref={heading} tabIndex={-1}>
                {state.done
                  ? "Everything reviewed."
                  : objective === "prepare" && journey === "multi"
                    ? "Two tasks. One plan."
                    : titles[objective]}
              </h1>
            </div>
            <span className="v4-task-count">
              {state.handoff!.draft.actions.length}{" "}
              {state.handoff!.draft.actions.length === 1 ? "task" : "tasks"}
            </span>
          </header>
          {state.error && (
            <p role="alert" className="v4-error">
              {state.error}
            </p>
          )}
          <div className="v4-status" role="status">
            <span>
              <i data-live={composition.source === "jev"} />
              {busy
                ? "Arranging your workspace…"
                : composition.source === "jev"
                  ? "Workspace ready"
                  : "Local layout"}
            </span>
            <span>
              {busy ? (
                <button onClick={cancel}>Cancel</button>
              ) : composition.source === "fallback" ? (
                <button onClick={() => void present(current.current, true)}>Try live layout</button>
              ) : (
                "Built around your task"
              )}
            </span>
          </div>
          {state.done ? (
            <section className="v4-done">
              <span>
                <Check size={28} />
              </span>
              <h2>Demo complete.</h2>
              <p>No money moved.</p>
              <button className="v4-secondary-button" onClick={() => turn(journey, 0)}>
                Start again <RotateCcw size={16} />
              </button>
            </section>
          ) : (
            <>
              <FinancialStatus state={state} />
              <V4Workspace
                key={state.handoff!.draft.draft_id}
                composition={composition}
                state={state}
                dispatch={dispatch}
              />
              {objective !== "review" && <ReviewBar state={state} dispatch={dispatch} />}
            </>
          )}
          <footer className="v4-footnote">
            {state.handoff!.data.source === "mock" ? "Mock accounts & quotes" : "Supplied account context"} ·
            DBS-inspired demo
          </footer>
        </div>
      </div>
      <details className="v4-inspector">
        <summary>
          Composition details <ChevronDown size={14} />
        </summary>
        <div>
          <span>{calls} evaluation requests</span>
          <span>{composition.elapsedMs} ms</span>
          <button disabled={busy || state.done} onClick={() => void present(current.current, true)}>
            Recompose
          </button>
        </div>
        <pre>
          {JSON.stringify(
            {
              context,
              cachedLayout,
              source: composition.source,
              reason: composition.reason,
              calls,
              elapsedMs: composition.elapsedMs,
              evaluations: composition.calls,
              spec: composition.spec,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}
