"use client";
import Link from "next/link";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowUpRight, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { V2Workspace, WinstonPlaceholder } from "@/components/v2-workspace";
import { conversationReducer, initialConversation } from "@/lib/v2-conversation";
import {
  composeBundles,
  handoffContext,
  livePresentationSchema,
  type Objective,
  type PresentationChoice,
} from "@/lib/v2-presentation";
import { winstonFixtures } from "@/lib/examples";
import type { z } from "zod";
import { v2Accounts } from "@/lib/v2-parser";
import { editedActions } from "@/lib/draft-editor";
import { post } from "@/lib/api";

export default function ConversationalBanking() {
  const [state, dispatch] = useReducer(conversationReducer, initialConversation);
  const [fixture, setFixture] = useState("transfer");
  const [objective, setObjective] = useState<Objective>("prepare");
  const [composing, setComposing] = useState(false);
  const [live, setLive] = useState<z.infer<typeof livePresentationSchema> | null>(null);
  const [previous, setPrevious] = useState<{ objective: Objective; selection: PresentationChoice }>();
  const [jevError, setJevError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const reduced = useReducedMotion();
  const surface = useRef<HTMLDivElement>(null);
  const presentation = useMemo(
    () => (state.draft ? composeBundles(state, objective, live ?? undefined, previous) : null),
    [state, objective, live, previous],
  );
  const valid =
    !!state.draft && !Object.keys(editedActions(state.draft, state.edits, v2Accounts).errors).length;
  useEffect(
    () => () => {
      generation.current++;
      controller.current?.abort();
    },
    [],
  );
  useEffect(() => {
    surface.current?.querySelector<HTMLElement>("[data-surface-heading]")?.focus();
  }, [state.surface]);
  function cancel() {
    generation.current++;
    controller.current?.abort();
    setComposing(false);
  }
  async function composeLive() {
    const chosen = winstonFixtures.find((f) => f.id === fixture)!;
    const event = { type: "handoff" as const, draft: chosen.draft, mode: chosen.mode };
    const next = conversationReducer(state, event);
    const prior =
      state.draft?.draft_id === chosen.draft.draft_id && presentation
        ? { objective, selection: presentation.trace.selection }
        : undefined;
    cancel();
    const current = generation.current;
    controller.current = new AbortController();
    dispatch(event);
    setObjective(chosen.objective);
    setPrevious(prior);
    setJevError("");
    if (!prior || prior.objective !== chosen.objective) setLive(null);
    setComposing(true);
    try {
      const context = handoffContext(next, chosen.objective, chosen.recipientStatus, prior?.selection);
      const response = await post(
        "/presentation-demo",
        livePresentationSchema,
        context,
        controller.current.signal,
      );
      if (current !== generation.current) return;
      const composed = composeBundles(next, chosen.objective, response, prior);
      if (composed.manifest.source !== "jev")
        throw new Error(composed.trace.fallbackReason ?? "Invalid presentation bundle.");
      setLive(response);
    } catch (error) {
      if (current !== generation.current) return;
      setLive(null);
      setJevError(error instanceof Error ? error.message : "JEV unavailable; local fallback used.");
    } finally {
      if (current === generation.current) setComposing(false);
    }
  }
  const title =
    state.surface === "review"
      ? "One final look."
      : state.surface === "result"
        ? "Your demo is reviewed."
        : objective === "assess_affordability"
          ? "See the impact of your plan."
          : objective === "check_recipient"
            ? "Check who you are paying."
            : "Your plan, ready to shape.";
  return (
    <div className="v2-shell">
      <nav className="v2-topline" aria-label="Experiment navigation">
        <span>DCTA LAB - V2</span>
        <Link href="/">
          Original workspace <ArrowUpRight size={14} />
        </Link>
      </nav>
      <div className="v2-demo-label">
        Mock Winston output and banking data -{" "}
        {composing
          ? "Calling live JEV..."
          : presentation?.manifest.source === "jev"
            ? `Live JEV - ${live?.systemone.model}`
            : "Local composition fallback"}
      </div>
      <WinstonPlaceholder
        selected={fixture}
        onSelect={setFixture}
        onCompose={() => void composeLive()}
        busy={composing}
      />
      {composing && (
        <div className="v2-jev-status" role="status">
          JEV is choosing presentation only.{" "}
          <Button variant="ghost" onClick={cancel}>
            Cancel composition
          </Button>
        </div>
      )}
      {jevError && (
        <p className="error-text" role="alert">
          {jevError} The workspace is using its local fallback.
        </p>
      )}
      <motion.div
        ref={surface}
        className="v2-surface"
        layout={reduced ? false : "position"}
        transition={{ duration: reduced ? 0 : 0.24, ease: "easeInOut" }}
      >
        {!state.draft ? (
          <section className="v2-context">
            <p className="eyebrow">AWAITING WINSTON OUTPUT</p>
            <h1>Start with a resolved task.</h1>
            <p>
              Winston handles conversation, task routing and missing details before handing a complete task to
              this workspace. Choose a mock output above to inspect the JEV presentation.
            </p>
          </section>
        ) : (
          <>
            <header className="v2-workspace-heading">
              <div>
                <p className="eyebrow">WINSTON OBJECTIVE: {objective.replaceAll("_", " ")}</p>
                <h1 data-surface-heading tabIndex={-1}>
                  {title}
                </h1>
                <p>Edit the details and review every action before confirming.</p>
              </div>
            </header>
            {state.surface !== "result" && presentation && (
              <V2Workspace
                state={state}
                manifest={presentation.manifest}
                dispatch={(event) => {
                  if (event.type === "edit") cancel();
                  dispatch(event);
                }}
              />
            )}
            {state.surface === "workspace" && (
              <div className="v2-action-bar">
                <p>
                  Further conversational instructions are resolved by Winston. The selector simulates his
                  updated output.
                </p>
                <Button disabled={!valid || composing} onClick={() => dispatch({ type: "review" })}>
                  Review my task
                </Button>
              </div>
            )}
            {state.surface === "review" && (
              <section className="v2-review">
                <h2>Check each action before confirming.</h2>
                {state.draft.actions.map((_, index) => (
                  <label key={index}>
                    <input
                      type="checkbox"
                      checked={!!state.approved[index]}
                      onChange={() => dispatch({ type: "approve", index })}
                    />{" "}
                    I have reviewed action {index + 1}
                  </label>
                ))}
                <div className="v2-action-bar">
                  <Button variant="ghost" onClick={() => dispatch({ type: "return" })}>
                    Back to editing
                  </Button>
                  <Button
                    disabled={!state.draft.actions.every((_, i) => state.approved[i])}
                    onClick={() => dispatch({ type: "finish" })}
                  >
                    Confirm demo task
                  </Button>
                </div>
                <p className="fine-print">
                  Demo review only. Real transactions require backend validation, signing and gateway
                  approval.
                </p>
              </section>
            )}
            {state.surface === "result" && (
              <section className="v2-result">
                <Check size={30} />
                <p>Demo review completed. No transaction was executed.</p>
                <Button
                  onClick={() => {
                    cancel();
                    setLive(null);
                    setPrevious(undefined);
                    dispatch({ type: "reset" });
                  }}
                >
                  Start a new task
                </Button>
              </section>
            )}
          </>
        )}
      </motion.div>
      {presentation && (
        <details className="v2-trace">
          <summary>How this workspace was composed</summary>
          <p>
            Bundle decisions are presentation only. Stability margin 0.15 is an experimental UI policy, not
            financial confidence.
          </p>
          <pre>{JSON.stringify({ ...presentation, systemone: live?.systemone ?? null }, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
