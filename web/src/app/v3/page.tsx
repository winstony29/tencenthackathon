"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronDown,
  CircleDot,
  RotateCcw,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { Button } from "@/components/ui/button";
import { V3Workspace } from "@/components/v3-workspace";
import {
  compose,
  objectiveLabels,
  replySchema,
  type Composition,
  type Manifest,
  type Reply,
} from "@/lib/v3-presentation";
import { candidates, makeContext, type Context } from "@/lib/v3-registry";
import {
  demoHandoff,
  effective,
  initialState,
  journeys,
  reducer,
  type Event,
  type State,
} from "@/lib/v3-state";
import { v2Accounts } from "@/lib/v2-parser";
import { exampleQuote } from "@/lib/examples";
import { post } from "@/lib/api";
import "./v3.css";

export default function V3() {
  const [state, setState] = useState<State>(initialState);
  const current = useRef(state);
  const [journey, setJourney] = useState<keyof typeof journeys>("transfer");
  const [step, setStep] = useState(-1);
  const [composition, setComposition] = useState<Composition | null>(null);
  const [context, setContext] = useState<Context | null>(null);
  const previous = useRef<{ context: Context; manifest: Manifest; draftId: string } | undefined>(undefined);
  const [reply, setReply] = useState<Reply | null>(null);
  const [busy, setBusy] = useState(false);
  const [calls, setCalls] = useState(0);
  const generation = useRef(0),
    controller = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const reduced = useReducedMotion();
  useEffect(
    () => () => {
      generation.current++;
      controller.current?.abort();
    },
    [],
  );
  useEffect(() => {
    if (state.task.surface === "review" || state.task.surface === "result") heading.current?.focus();
  }, [state.task.surface]);
  function cancel() {
    generation.current++;
    controller.current?.abort();
    setBusy(false);
  }
  function update(next: State) {
    current.current = next;
    setState(next);
  }
  async function present(next: State) {
    cancel();
    if (!next.task.draft) return;
    const draftId = next.task.draft.draft_id;
    const ctx = makeContext(next.task.draft, next.objective, v2Accounts, { AAPL: exampleQuote });
    const prior =
      previous.current?.draftId === draftId &&
      previous.current.context.objective === ctx.objective &&
      JSON.stringify(previous.current.context.action_types) === JSON.stringify(ctx.action_types)
        ? previous.current
        : undefined;
    if (prior)
      ctx.current_ui = {
        primary: prior.manifest.nodes.find((n) => n.slot === "primary")!.key,
        components: prior.manifest.nodes.map((n) => n.key),
        layout: prior.manifest.layout,
        density: prior.manifest.density,
      };
    const local = compose(ctx);
    setContext(ctx);
    setComposition(local);
    setReply(null);
    setBusy(true);
    setCalls((n) => n + 1);
    const request = generation.current;
    controller.current = new AbortController();
    try {
      const response = await post("/presentation-v3", replySchema, ctx, controller.current.signal);
      if (request !== generation.current) return;
      const result = compose(ctx, response, prior);
      setReply(response);
      setComposition(result);
      previous.current = { context: ctx, manifest: result.manifest, draftId };
    } catch {
      if (request !== generation.current) return;
      setComposition({ ...local, rejected: "Live composition unavailable. Local layout ready." });
      previous.current = { context: ctx, manifest: local.manifest, draftId };
    } finally {
      if (request === generation.current) setBusy(false);
    }
  }
  function turn(nextJourney: keyof typeof journeys, index: number) {
    const base = nextJourney === journey && index > 0 ? current.current : initialState;
    if (index === 0 || nextJourney !== journey) previous.current = undefined;
    const next = reducer(base, { type: "handoff", value: demoHandoff(nextJourney, index, base) });
    setJourney(nextJourney);
    setStep(index);
    update(next);
    if (!next.error) void present(next);
  }
  function dispatch(event: Event) {
    const next = reducer(current.current, event);
    if (next === current.current) return;
    if (["edit", "scenario", "finish", "reset"].includes(event.type)) cancel();
    update(next);
    if (["review", "back", "apply"].includes(event.type)) void present(next);
  }
  const valid = !!state.task.draft && !Object.keys(effective(state).errors).length;
  const turns = journeys[journey];
  const done = state.task.surface === "result";
  const status = busy
    ? "Shaping your workspace"
    : composition?.manifest.source === "jev"
      ? "Live JEV"
      : "Local layout";
  return (
    <div className="v3-shell">
      <nav className="v3-nav" aria-label="Workspace versions">
        <span>
          PERSONAL BANKING <span className="v3-nav-slash">/</span> V3
        </span>
        <div>
          <Link href="/v2/">
            V2 <ArrowUpRight size={13} />
          </Link>
          <span className="v3-demo-pill">Demo · no real transactions</span>
        </div>
      </nav>
      <div className="v3-stage">
        <aside className="v3-conversation" aria-label="Mock Winston journey">
          <div className="v3-journal-title">
            <span className="v3-kicker">YOUR CONVERSATION</span>
            <CircleDot size={16} aria-hidden="true" />
          </div>
          <div className="v3-journey-tabs" role="group" aria-label="Demo journey">
            <button aria-pressed={journey === "transfer"} onClick={() => turn("transfer", 0)}>
              Transfer
            </button>
            <button aria-pressed={journey === "multi"} onClick={() => turn("multi", 0)}>
              Pay & invest
            </button>
          </div>
          <p className="v3-journal-note">Resolved demo turns from Winston</p>
          <ol className="v3-turns">
            {turns.map((t, i) => (
              <li key={t.text} data-active={step === i} data-past={step > i}>
                <button aria-current={step === i ? "step" : undefined} onClick={() => turn(journey, i)}>
                  <span className="v3-turn-index">
                    {step > i ? <Check size={12} /> : String(i + 1).padStart(2, "0")}
                  </span>
                  <span>
                    <small>{t.label}</small>
                    <span>{t.text}</span>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="v3-journal-bottom">
            <CheckCheck size={16} aria-hidden="true" />
            <p>
              Details resolved upstream.
              <br />
              You stay in control.
            </p>
          </div>
        </aside>
        <section className="v3-canvas" aria-label="Banking workspace">
          <header className="v3-canvas-header">
            <div>
              <p className="v3-kicker">
                {done
                  ? "ALL SET"
                  : state.objective === "simulate"
                    ? "EXPLORE, THEN DECIDE"
                    : "YOUR NEXT MOVE"}
              </p>
              <h1 ref={heading} tabIndex={-1}>
                {done
                  ? "Review complete."
                  : state.task.draft
                    ? objectiveLabels[state.objective]
                    : "Banking, in focus."}
              </h1>
            </div>
            <span className="v3-session">
              {state.task.draft
                ? `${state.task.draft.actions.length} ${state.task.draft.actions.length === 1 ? "task" : "tasks"}`
                : "Start here"}
            </span>
          </header>
          {state.error && (
            <p className="v3-negative" role="alert">
              {state.error}
            </p>
          )}
          {!state.task.draft ? (
            <div className="v3-welcome">
              <div className="v3-welcome-symbol" aria-hidden="true">
                <ArrowUpRight size={56} strokeWidth={1} />
              </div>
              <h2>
                A plan that moves
                <br />
                with you.
              </h2>
              <p>
                Send. Explore. Review.
                <br />
                One workspace for your next decision.
              </p>
              <Button onClick={() => turn(journey, 0)}>
                Start a transfer <ArrowRight size={16} />
              </Button>
            </div>
          ) : done ? (
            <motion.div
              initial={reduced ? false : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className="v3-done"
            >
              <span>
                <Check size={32} />
              </span>
              <h2>Everything reviewed.</h2>
              <p>Demo complete. No money moved.</p>
              <Button variant="outline" onClick={() => turn(journey, 0)}>
                Start again <RotateCcw size={15} />
              </Button>
            </motion.div>
          ) : (
            composition && (
              <>
                <div className="v3-composition-status" role="status">
                  <span>
                    <i data-live={composition.manifest.source === "jev"} />
                    {status}
                  </span>
                  {busy ? (
                    <button onClick={cancel}>Cancel</button>
                  ) : composition.manifest.source === "fallback" ? (
                    <button onClick={() => void present(current.current)}>Retry JEV</button>
                  ) : (
                    <span>Built around your task</span>
                  )}
                </div>
                <V3Workspace state={state} manifest={composition.manifest} dispatch={dispatch} />
                {state.task.surface === "workspace" && (
                  <footer className="v3-plan-footer">
                    <div>
                      <span className="v3-lock-dot" />
                      <span>
                        {Object.keys(state.scenario).length
                          ? "Review uses your draft, not this scenario."
                          : "Your approval comes next."}
                      </span>
                    </div>
                    <Button disabled={!valid} onClick={() => dispatch({ type: "review" })}>
                      Review plan <ArrowRight size={16} />
                    </Button>
                  </footer>
                )}
              </>
            )
          )}
        </section>
      </div>
      <footer className="v3-bottom">
        <span>DCTA / Tencent Cloud Hackathon</span>
        <span>Mock accounts & quotes · DBS-inspired demo</span>
      </footer>
      {context && composition && (
        <details className="v3-inspector">
          <summary>
            <span>Developer inspector</span>
            <ChevronDown size={14} />
          </summary>
          <div className="v3-inspector-meta">
            <span>{calls} JEV calls</span>
            <span>{reply ? `${reply.latency_ms} ms` : "No live response"}</span>
            <span>{composition.manifest.source}</span>
            <button onClick={() => void present(current.current)} disabled={busy || done}>
              Recompose
            </button>
          </div>
          <pre>
            {JSON.stringify(
              {
                objective: context.objective,
                eligible: candidates(context),
                selection: reply?.answers ?? null,
                model: reply?.model ?? null,
                manifest: composition.manifest,
                rejected: composition.rejected,
                stability: composition.stability,
                changes: composition.changes,
                calls,
                latency_ms: reply?.latency_ms ?? null,
              },
              null,
              2,
            )}
          </pre>
        </details>
      )}
      <Link className="v3-original" href="/">
        <ArrowLeft size={12} /> Original workspace
      </Link>
    </div>
  );
}
