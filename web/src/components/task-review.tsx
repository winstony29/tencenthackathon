"use client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ArrowRight, CheckCircle2 } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { WorkspaceRenderer } from "@/components/workspace-renderer";
import type { ComponentProps } from "react";
import { ActionCard } from "@/components/action-card";
import type { Draft, Quote, Understand, Validation } from "@/lib/contracts";

type Props = {
  draft: Draft | null;
  composition?: Omit<ComponentProps<typeof WorkspaceRenderer>, "draft">;
  allApproved: boolean;
  hasEdits: boolean;
  demo: boolean;
  reply: Understand | null;
  busy: boolean;
  preview: string;
  text: string;
  quotes: Record<string, Quote>;
  reviewed: boolean;
  validation: Validation | null;
  confirmed: boolean;
  onAnswer: (id: string) => void;
  onCorrect: () => void;
  onReview: () => void;
  onReviewedChange: (checked: boolean) => void;
};
export function TaskReview({
  draft,
  composition,
  allApproved,
  hasEdits,
  demo,
  reply,
  busy,
  preview,
  text,
  quotes,
  reviewed,
  validation,
  confirmed,
  onAnswer,
  onCorrect,
  onReview,
  onReviewedChange,
}: Props) {
  const reducedMotion = useReducedMotion();
  return (
    <>
      <AnimatePresence initial={false} mode="wait">
        <motion.div
          key={draft?.draft_id ?? reply?.status ?? "empty"}
          initial={{ opacity: reducedMotion ? 1 : 0, y: reducedMotion ? 0 : 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          transition={{ duration: reducedMotion ? 0 : 0.2 }}
        >
          {reply?.status === "question" && !demo && (
            <div className="question-card">
              <span className="eyebrow">LET’S GET THIS RIGHT</span>
              <h2>{reply.question.text}</h2>
              <p>Choose the person you meant. We’ll use your answer to build the draft.</p>
              <div className="answer-options">
                {reply.question.options.map((option) => (
                  <Button
                    variant="outline"
                    key={option.option_id}
                    disabled={busy}
                    onClick={() => onAnswer(option.option_id)}
                  >
                    {option.label}
                    <ArrowRight size={18} aria-hidden="true" />
                  </Button>
                ))}
              </div>
              {reply.question.allow_none && (
                <Button
                  variant="link"
                  className="text-button"
                  disabled={busy}
                  onClick={() => onAnswer("none")}
                >
                  None of these
                </Button>
              )}
              <Button variant="link" className="text-button" onClick={onCorrect}>
                Change request
              </Button>
            </div>
          )}
          {(reply?.status === "blocked" ||
            reply?.status === "unsupported" ||
            reply?.status === "cancelled") && (
            <div className="notice">
              <h2>
                {reply.status === "blocked"
                  ? "Please rephrase your request"
                  : reply.status === "unsupported"
                    ? "Try another banking task"
                    : "Request cancelled"}
              </h2>
              <p>{reply.message}</p>
              <Button variant="link" className="text-button" onClick={onCorrect}>
                Write a new request →
              </Button>
            </div>
          )}
          {reply?.status === "answer" && (
            <div className="notice">
              <h2>Here’s what I found</h2>
              <p>Answers to questions about your accounts show in the main app at /v5/.</p>
              <Button variant="link" className="text-button" onClick={onCorrect}>
                Write a new request →
              </Button>
            </div>
          )}
          {draft ? (
            <>
              {composition ? (
                <WorkspaceRenderer draft={draft} {...composition} />
              ) : (
                <div className="cards">
                  {draft.actions.map((action, index) => (
                    <ActionCard
                      key={index}
                      action={action}
                      index={index}
                      quote={action.type === "stock_order" ? quotes[action.ticker] : undefined}
                    />
                  ))}
                </div>
              )}
              <motion.div className="review-footer" layout={reducedMotion ? false : "position"}>
                <motion.label
                  className="review-checkbox"
                  animate={{ borderColor: reviewed ? "#d50000" : "#e2e5e9" }}
                  transition={{ duration: reducedMotion ? 0 : 0.2 }}
                >
                  <Checkbox
                    checked={reviewed}
                    onCheckedChange={onReviewedChange}
                    disabled={busy || !allApproved}
                  />
                  <span>I’ve checked every action and its details.</span>
                </motion.label>
                <div className="review-actions">
                  <Button variant="link" className="text-button" onClick={onCorrect}>
                    Change request
                  </Button>
                  <Button
                    className="primary-button"
                    disabled={!reviewed || busy || !allApproved}
                    onClick={() => onReview()}
                  >
                    {demo ? "Review example" : "Check draft"}
                    <ArrowRight size={18} aria-hidden="true" />
                  </Button>
                </div>
                <p className="fine-print">
                  {hasEdits
                    ? "Local changes clear approvals. API edits require a new backend draft before signing."
                    : "Approve each action, then review the complete plan. Bank validation and signing remain mandatory."}
                </p>
              </motion.div>
            </>
          ) : (
            !reply && (
              <div className="empty-task">
                <span className="empty-symbol" aria-hidden="true">
                  {preview === "stock_order" ? "↗" : preview === "bill_payment" ? "≡" : "⇄"}
                </span>
                <h2>
                  {text.trim().length > 1 && preview !== "none"
                    ? `A ${preview.replaceAll("_", " ")} page is taking shape.`
                    : "Your next task starts here."}
                </h2>
                <p>
                  {text.trim().length > 1
                    ? "Submit your request to clarify the details and build a draft."
                    : "Transfers, bills, or shares. Start with a request above, or explore an example."}
                </p>
              </div>
            )
          )}
        </motion.div>
      </AnimatePresence>
      {validation?.status === "mismatch" && (
        <div className="notice error-notice" role="alert">
          <h2>Draft frozen</h2>
          <p>Some details don’t match your request. Nothing has been sent.</p>
          {validation.fields.map((field) => (
            <p key={field.field}>
              <strong>{field.field}</strong>: {field.draft_value} · You said: {field.transcript_says}.{" "}
              {field.note}
            </p>
          ))}
          <Button variant="link" className="text-button" onClick={onCorrect}>
            Correct my request
          </Button>
        </div>
      )}
      {(validation?.status === "match" || confirmed) && (
        <motion.div
          className="notice success-notice"
          role="status"
          initial={{ opacity: reducedMotion ? 1 : 0, y: reducedMotion ? 0 : 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: reducedMotion ? 0 : 0.25 }}
        >
          <motion.span
            className="success-icon"
            aria-hidden="true"
            initial={reducedMotion ? false : { scale: 0.7, rotate: -20 }}
            animate={{ scale: 1, rotate: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 18 }}
          >
            <CheckCircle2 size={22} />
          </motion.span>
          <strong>{demo ? "Example reviewed." : "The validation stub returned a match."}</strong>
          <p>
            {demo
              ? "This sample is for exploring the page. It cannot be sent."
              : "Signing is the next step to connect. No transaction was executed."}
          </p>
        </motion.div>
      )}
      {reply && (
        <p className="fine-print metadata">
          Response: {reply.meta.source} · {reply.meta.latency_ms} ms · {reply.meta.phrasings} phrasings
        </p>
      )}
    </>
  );
}
