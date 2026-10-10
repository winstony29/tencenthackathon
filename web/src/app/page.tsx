"use client";
import { Button } from "@/components/ui/button";

import { useCallback, useEffect, useRef, useState } from "react";
import { MotionConfig } from "motion/react";
import { TaskReview } from "@/components/task-review";
import { CommandInput } from "@/components/command-input";
import { ContextPanel, WelcomePanel } from "@/components/context-panel";
import { post, request } from "@/lib/api";
import { exampleDraft, examples } from "@/lib/examples";
import {
  previewSchema,
  transcribeSchema,
  understandSchema,
  validationSchema,
  type Draft,
  type Quote,
  type Understand,
  type Validation,
} from "@/lib/contracts";

export default function BankingPage() {
  const [text, setText] = useState("");
  const [reply, setReply] = useState<Understand | null>(null);
  const [demo, setDemo] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("none");
  const [validation, setValidation] = useState<Validation | null>(null);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [reviewed, setReviewed] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [commandVersion, setCommandVersion] = useState(0);
  const session = useRef("");
  const active = useRef<AbortController | null>(null);
  const draft = demo ?? (reply?.status === "draft" ? reply.draft : null);
  const updateQuotes = useCallback((data: Record<string, Quote>) => setQuotes(data), []);

  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => {
    if (text.trim().length < 2 || draft || reply || busy) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      post("/preview", previewSchema, { text }, controller.signal)
        .then((data) => {
          if (!controller.signal.aborted) setPreview(data.intent.value);
        })
        .catch(() => {
          /* Display-only preview keeps its last state on upstream failure. */
        });
    }, 150);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text, draft, reply, busy]);

  function reset(clearText = false) {
    active.current?.abort();
    active.current = null;
    session.current = "";
    setCommandVersion((version) => version + 1);
    setReply(null);
    setDemo(null);
    setValidation(null);
    setQuotes({});
    setReviewed(false);
    setConfirmed(false);
    setBusy(false);
    setError("");
    setPreview("none");
    if (clearText) setText("");
  }
  function changeText(value: string) {
    setText(value);
    if (value.trim().length < 2) setPreview("none");
  }
  function startRequest() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setError("");
    setValidation(null);
    setConfirmed(false);
    return controller;
  }
  async function understand(optionId?: string, audio?: Blob) {
    const controller = startRequest();
    try {
      let transcript = text.trim();
      if (audio) {
        const body = new FormData();
        body.append("audio", audio, "recording");
        const result = await request("/transcribe", transcribeSchema, {
          method: "POST",
          body,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        transcript = result.transcript;
        setText(transcript);
      }
      if (!session.current || !optionId) session.current = `s_${crypto.randomUUID().replaceAll("-", "")}`;
      const body =
        optionId && reply?.status === "question"
          ? {
              user_id: "u_demo",
              session_id: session.current,
              answer: { question_id: reply.question.question_id, option_id: optionId },
            }
          : { user_id: "u_demo", session_id: session.current, transcript };
      const result = await post("/understand", understandSchema, body, controller.signal);
      if (controller.signal.aborted) return;
      setDemo(null);
      setReply(result);
      setQuotes({});
      setReviewed(false);
      if (result.status !== "question") session.current = "";
    } catch (e) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : "Could not understand the request.");
        session.current = "";
        setReply(null);
      }
    } finally {
      if (active.current === controller) setBusy(false);
    }
  }
  function showExample(index: number) {
    reset();
    const example = exampleDraft(index);
    setText(example.transcript);
    setDemo(example);
  }
  function correctRequest() {
    reset();
    requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>("#command")?.focus());
  }
  async function checkDraft() {
    if (!draft || !reviewed) return;
    if (demo) {
      setConfirmed(true);
      return;
    }
    const controller = startRequest();
    try {
      const result = await post("/validate", validationSchema, { draft }, controller.signal);
      if (!controller.signal.aborted) setValidation(result);
    } catch (e) {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : "Could not validate the draft.");
    } finally {
      if (active.current === controller) setBusy(false);
    }
  }
  const stage = busy
    ? "Working on your request"
    : draft
      ? "Review your task"
      : reply?.status === "question"
        ? "One quick question"
        : "Start with your words";
  return (
    <MotionConfig reducedMotion="user">
      <>
        <div className="prototype-notice">
          <span className="status-dot" />
          <span>
            {demo
              ? "Example mode — sample details for exploring the interface."
              : "API stub mode — responses are fixed examples."}{" "}
            No money moves in this prototype.
          </span>
        </div>
        <div className={`workspace ${draft ? "has-task" : ""}`}>
          <section className="main-panel" aria-label="Banking task">
            <div className="section-heading">
              <span className="step-number">01</span>
              <h2>Your request</h2>
              <Button variant="link" className="text-button" onClick={() => reset(true)}>
                Start over
              </Button>
            </div>
            <CommandInput
              key={commandVersion}
              text={text}
              onChange={changeText}
              onSubmit={() => void understand()}
              onAudio={(audio) => {
                reset();
                void understand(undefined, audio);
              }}
              loading={busy}
              disabled={busy || !!draft || reply?.status === "question"}
            />
            <div className="example-list">
              <span>Explore an example</span>
              {examples.map((example, i) => (
                <Button
                  variant="outline"
                  size="sm"
                  key={example.label}
                  onClick={() => showExample(i)}
                  disabled={busy}
                >
                  {example.label}
                  <span aria-hidden="true"> ↗</span>
                </Button>
              ))}
            </div>
            <div className="task-stage" role="status" aria-live="polite">
              <span className="step-number">02</span>
              <span>{stage}</span>
              {busy && <span className="loading-dot" />}
              {busy && (
                <Button variant="link" className="text-button" onClick={() => reset()}>
                  Cancel
                </Button>
              )}
            </div>
            {error && (
              <div className="notice error-notice" role="alert">
                <strong>We couldn’t finish that step.</strong>
                <p>{error}</p>
                <Button variant="link" className="text-button" onClick={correctRequest}>
                  Change request
                </Button>
              </div>
            )}
            <TaskReview
              draft={draft}
              demo={!!demo}
              reply={reply}
              busy={busy}
              preview={preview}
              text={text}
              quotes={quotes}
              reviewed={reviewed}
              validation={validation}
              confirmed={confirmed}
              onAnswer={(id) => void understand(id)}
              onCorrect={correctRequest}
              onReview={() => void checkDraft()}
              onReviewedChange={(checked) => {
                setReviewed(checked);
                setValidation(null);
                setConfirmed(false);
              }}
            />
          </section>
          {draft ? (
            <ContextPanel
              key={`${demo ? "demo" : "api"}-${draft.draft_id}`}
              draft={draft}
              demo={!!demo}
              onQuotes={updateQuotes}
            />
          ) : (
            <WelcomePanel />
          )}
        </div>
      </>
    </MotionConfig>
  );
}
