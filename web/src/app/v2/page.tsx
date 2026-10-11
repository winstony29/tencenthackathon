"use client";
import Link from "next/link";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ArrowLeft, ArrowUpRight, Check, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CommandInput } from "@/components/command-input";
import { V2Workspace, WinstonPlaceholder } from "@/components/v2-workspace";
import { conversationReducer, initialConversation } from "@/lib/v2-conversation";
import { livePresentationSchema, present } from "@/lib/v2-presentation";
import { winstonFixtures } from "@/lib/examples";
import type { z } from "zod";
import { v2Accounts } from "@/lib/v2-parser";
import { editedActions } from "@/lib/draft-editor";
import { request } from "@/lib/api";
import { transcribeSchema } from "@/lib/contracts";

export default function ConversationalBanking() {
  const [state, dispatch] = useReducer(conversationReducer, initialConversation);
  const [text, setText] = useState("");
  const [audioError, setAudioError] = useState("");
  const [fixture, setFixture] = useState("transfer");
  const [composing, setComposing] = useState(false);
  const [live, setLive] = useState<z.infer<typeof livePresentationSchema> | null>(null);
  const [jevError, setJevError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const activeFixture = useRef<string | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const reduced = useReducedMotion();
  const presentation = useMemo(() => {
    const result = present(state, live?.selection);
    if (jevError) result.trace.fallbackReason = jevError;
    return result;
  }, [state, live, jevError]);
  const chat = state.surface === "conversation" || state.surface === "refinement";
  const valid =
    state.draft && !Object.keys(editedActions(state.draft, state.edits, v2Accounts).errors).length;
  useEffect(
    () => () => {
      generation.current++;
      controller.current?.abort();
    },
    [],
  );
  useEffect(() => {
    if (chat) surface.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    else surface.current?.querySelector<HTMLElement>("[data-surface-heading]")?.focus();
  }, [state.surface, chat]);
  useEffect(() => {
    console.debug("DCTA V2 presentation", { ...presentation.trace, manifest: presentation.manifest });
  }, [presentation]);
  function cancel() {
    generation.current++;
    controller.current?.abort();
    setAudioError("");
    setComposing(false);
    dispatch({ type: "return" });
  }
  async function submit(command = text) {
    if (!command.trim()) return;
    const current = ++generation.current;
    controller.current?.abort();
    setComposing(false);
    setLive(null);
    setJevError("");
    dispatch({ type: "begin" });
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    if (current !== generation.current) return;
    dispatch({ type: "command", text: command.trim() });
    setText("");
  }
  async function audio(blob: Blob) {
    const current = ++generation.current;
    setLive(null);
    setJevError("");
    setComposing(false);
    controller.current?.abort();
    controller.current = new AbortController();
    const data = new FormData();
    data.append("audio", blob, "command.webm");
    dispatch({ type: "begin" });
    setAudioError("");
    try {
      const response = await request("/transcribe", transcribeSchema, {
        method: "POST",
        body: data,
        signal: controller.current.signal,
      });
      if (current !== generation.current) return;
      dispatch({ type: "command", text: response.transcript });
      setText("");
    } catch (error) {
      if (current !== generation.current) return;
      dispatch({ type: "return" });
      setAudioError(error instanceof Error ? error.message : "Transcription unavailable. Type your request.");
    }
  }
  async function composeLive() {
    const chosen = winstonFixtures.find((f) => f.id === fixture)!;
    const event = {
      type: "load" as const,
      draft: chosen.draft,
      partial: chosen.partial ? { ...chosen.partial, candidates: [...chosen.partial.candidates] } : null,
      mode: chosen.mode,
    };
    const reuse =
      activeFixture.current === fixture &&
      state.draft?.draft_id === chosen.draft?.draft_id &&
      state.mode === chosen.mode &&
      !!state.partial === !!chosen.partial &&
      !!state.draft === !!chosen.draft;
    const next = reuse ? state : conversationReducer(state, event);
    const current = ++generation.current;
    controller.current?.abort();
    controller.current = new AbortController();
    if (!reuse) dispatch(event);
    activeFixture.current = fixture;
    setLive(null);
    setJevError("");
    setComposing(true);
    try {
      const response = await request(
        `/presentation-demo?fixture=${encodeURIComponent(fixture)}`,
        livePresentationSchema,
        { method: "POST", signal: controller.current.signal },
      );
      if (current !== generation.current) return;
      const composed = present(next, response.selection);
      if (composed.manifest.source !== "jev")
        throw new Error(composed.trace.fallbackReason ?? "Ineligible JEV selection.");
      setLive(response);
    } catch (error) {
      if (current !== generation.current) return;
      setJevError(error instanceof Error ? error.message : "JEV unavailable; local fallback used.");
    } finally {
      if (current === generation.current) setComposing(false);
    }
  }
  const title =
    state.surface === "partial"
      ? !state.partial?.recipient
        ? "Which John?"
        : "How much would you like to send?"
      : state.surface === "review"
        ? "One final look."
        : state.surface === "result"
          ? "Your demo is reviewed."
          : state.mode === "decision"
            ? "Let’s look at the bigger picture."
            : state.draft?.actions.length === 1
              ? state.draft.actions[0].type === "transfer"
                ? "Your transfer, your way."
                : state.draft.actions[0].type === "bill_payment"
                  ? "Your bill, without the detour."
                  : "Your next investment move."
              : "Two moves. One workspace.";
  return (
    <div className="v2-shell">
      <nav className="v2-topline" aria-label="Experiment navigation">
        <span>
          <span className="v2-status-dot" />
          DCTA LAB · V2
        </span>
        <Link href="/">
          Original workspace <ArrowUpRight size={14} />
        </Link>
      </nav>
      <div className="v2-demo-label">
        Mock Winston output and banking data ·{" "}
        {composing
          ? "Calling live JEV…"
          : presentation.manifest.source === "jev"
            ? `Live JEV · ${live?.systemone.model}`
            : "Local composition fallback"}
      </div>
      <WinstonPlaceholder
        selected={fixture}
        onSelect={setFixture}
        onCompose={() => void composeLive()}
        busy={composing}
      />
      {composing && (
        <p className="v2-jev-status" role="status">
          JEV is selecting the presentation. Task values remain unchanged.
        </p>
      )}
      {jevError && (
        <p className="error-text" role="alert">
          {jevError} The workspace is using its local fallback.
        </p>
      )}
      <AnimatePresence initial={false} mode="wait">
        <motion.div
          key={state.surface}
          ref={surface}
          className={`v2-surface ${chat ? "v2-conversation" : ""}`}
          initial={{ opacity: 0, y: reduced ? 0 : 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: reduced ? 0 : -8 }}
          transition={{ duration: reduced ? 0 : 0.2, ease: "easeOut" }}
          onAnimationComplete={() => {
            if (chat) surface.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
            else surface.current?.querySelector<HTMLElement>("[data-surface-heading]")?.focus();
          }}
        >
          {chat ? (
            <>
              {state.surface === "refinement" && (
                <Button variant="ghost" onClick={cancel}>
                  <ArrowLeft size={16} /> Back to workspace
                </Button>
              )}
              <p className="eyebrow">
                {state.surface === "refinement"
                  ? "KEEP THE TASK. CHANGE THE DETAILS."
                  : "A LITTLE LESS BANKING. A LITTLE MORE LIVING."}
              </p>
              <h1>
                {state.surface === "refinement" ? (
                  <>
                    What would you
                    <br />
                    <span>like to change?</span>
                  </>
                ) : (
                  <>
                    Your words.
                    <br />
                    <span>Your next move.</span>
                  </>
                )}
              </h1>
              <p className="v2-lead">
                {state.surface === "refinement"
                  ? "Ask about affordability, change an amount, or name a recipient. Your other edits stay with you."
                  : "Tell us what you want to do. We’ll shape a workspace around it."}
              </p>
              <CommandInput
                text={text}
                onChange={setText}
                onSubmit={() => void submit()}
                onAudio={(blob) => void audio(blob)}
                disabled={false}
                loading={false}
                placeholder={
                  state.surface === "refinement" ? "Actually, make it $300…" : "Transfer $500 to John…"
                }
                submitLabel={state.surface === "refinement" ? "Update my workspace" : "Shape my workspace"}
              />
              {(state.error || audioError) && (
                <p role="alert" className="error-text">
                  {state.error || audioError}
                </p>
              )}
              <div className="v2-prompts" aria-label="Try a request">
                {(state.draft || state.partial
                  ? ["Can I afford that?", "Actually, make it $300 from savings"]
                  : [
                      "Send money to John",
                      "Transfer $500 to John",
                      "Pay my credit card bill and buy 100 Apple shares",
                    ]
                ).map((prompt) => (
                  <button key={prompt} onClick={() => void submit(prompt)}>
                    {prompt}
                    <ArrowUpRight size={14} />
                  </button>
                ))}
              </div>
              <p className="fine-print">
                Numbers and listed names are parsed by deterministic demo code. Voice uses the existing
                transcription stub.
              </p>
            </>
          ) : state.surface === "interpreting" ? (
            <div className="v2-interpreting" role="status">
              <span className="v2-status-dot" />
              <h1 data-surface-heading tabIndex={-1}>
                Shaping your next move…
              </h1>
              <p>Resolving the task and choosing approved UI elements.</p>
              <Button variant="ghost" onClick={cancel}>
                Cancel
              </Button>
            </div>
          ) : (
            <>
              <div className="v2-workspace-heading">
                <div>
                  <p className="eyebrow">
                    {state.surface === "review"
                      ? "YOUR APPROVAL COMES HERE"
                      : state.surface === "result"
                        ? "DEMO COMPLETE"
                        : "SHAPED AROUND YOUR INTENT"}
                  </p>
                  <h1 data-surface-heading tabIndex={-1}>
                    {title}
                  </h1>
                  <p>
                    {state.surface === "partial"
                      ? "We’ll keep what we know while you fill in the missing details."
                      : state.surface === "result"
                        ? "Nothing was sent, signed or executed. This result belongs to the frontend demo only."
                        : "Edit the details. Ask another question. Stay in control."}
                  </p>
                </div>
                {state.surface !== "result" && (
                  <Button
                    variant="outline"
                    onClick={() => {
                      cancel();
                      setText("");
                      dispatch({ type: "chat" });
                    }}
                  >
                    <MessageSquare size={16} /> Refine in conversation
                  </Button>
                )}
              </div>
              {state.surface === "partial" && state.partial && (
                <div className="v2-partial-grid">
                  <section className="v2-context">
                    <p className="eyebrow">TRANSFER DETAILS</p>
                    <h2>A few details, then you’re ready.</h2>
                    <fieldset className="transaction-fields">
                      <legend className="sr-only">Complete your transfer</legend>
                      <label>
                        Recipient
                        <select
                          aria-label="Choose recipient"
                          value={state.partial.recipient ?? ""}
                          onChange={(e) =>
                            dispatch({ type: "clarify", field: "recipient", value: e.target.value })
                          }
                        >
                          <option value="">Choose the right John</option>
                          {v2Accounts.payees
                            .filter((p) => state.partial!.candidates.includes(p.payee_id))
                            .map((p) => (
                              <option key={p.payee_id} value={p.payee_id}>
                                {p.label}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label>
                        Amount (SGD)
                        <input
                          aria-label="Missing transfer amount"
                          inputMode="decimal"
                          placeholder="Enter an amount, e.g. 500.00"
                          value={state.partial.amount ?? ""}
                          onChange={(e) =>
                            dispatch({ type: "clarify", field: "amount", value: e.target.value })
                          }
                        />
                        <small>Use two decimals. No amount is assumed.</small>
                      </label>
                    </fieldset>
                  </section>
                  <section className="v2-context v2-context-primary">
                    <p className="eyebrow">WHAT WE KNOW</p>
                    <h2>Your transfer stays here.</h2>
                    <p>
                      From{" "}
                      {v2Accounts.accounts.find((a) => a.account_id === state.partial?.source)?.label ??
                        "Account not selected"}
                    </p>
                    <p>{state.partial.amount ? `SGD ${state.partial.amount}` : "Amount still needed"}</p>
                    <p>Two listed Johns. You choose the recipient.</p>
                  </section>
                </div>
              )}
              {state.draft && state.surface !== "result" && (
                <V2Workspace state={state} manifest={presentation.manifest} dispatch={dispatch} />
              )}
              {state.surface === "workspace" && (
                <div className="v2-action-bar">
                  <p>Ready when you are. Review comes before confirmation.</p>
                  <Button disabled={!valid || composing} onClick={() => dispatch({ type: "review" })}>
                    Review my task <ArrowUpRight size={16} />
                  </Button>
                </div>
              )}
              {state.surface === "review" && (
                <section className="v2-review">
                  <h2>Check each action before confirming.</h2>
                  {state.draft?.actions.map((_, index) => (
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
                      disabled={!state.draft?.actions.every((_, i) => state.approved[i])}
                      onClick={() => dispatch({ type: "finish" })}
                    >
                      Confirm demo task
                    </Button>
                  </div>
                  <p className="fine-print">
                    This confirms the demo review only. A real transaction requires backend validation,
                    signing and gateway approval.
                  </p>
                </section>
              )}
              {state.surface === "result" && (
                <div className="v2-result">
                  <Check size={30} />
                  <p>You remained in control, from your first words to your final review.</p>
                  <Button
                    onClick={() => {
                      cancel();
                      setLive(null);
                      setJevError("");
                      dispatch({ type: "reset" });
                    }}
                  >
                    Start a new conversation
                  </Button>
                </div>
              )}
              {state.error && (
                <p role="alert" className="error-text">
                  {state.error}
                </p>
              )}
            </>
          )}
        </motion.div>
      </AnimatePresence>
      <details className="v2-trace">
        <summary>How this workspace was composed</summary>
        <p>
          Presentation choices only. “Execute” describes a review-ready interface and grants no transaction
          authority.
        </p>
        <pre>{JSON.stringify({ ...presentation, systemone: live?.systemone ?? null }, null, 2)}</pre>
      </details>
    </div>
  );
}
