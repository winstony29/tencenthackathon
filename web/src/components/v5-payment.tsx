"use client";
import { useEffect, useRef, useState } from "react";
import { Check, LockKeyhole } from "lucide-react";
import type { Draft } from "@/lib/contracts";
import {
  preparePayment,
  submitPayment,
  type Payment,
  type Execution,
} from "@/lib/v5-bank";

export function V5Payment({
  draft,
  onCancel,
  onLock,
  onSettled,
  preview = false,
}: {
  preview?: boolean;
  draft: Draft;
  onCancel: () => void;
  onLock: (locked: boolean) => void;
  onSettled: (completed: boolean) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [result, setResult] = useState<Execution | null>(null),
    [otp, setOtp] = useState("");
  const pending = useRef<Payment | null>(null),
    locked = useRef(false);
  const abort = useRef<AbortController | null>(null);
  const [uncertain, setUncertain] = useState(false);
  useEffect(() => () => abort.current?.abort(), []);
  const a = draft.actions[0];
  const label =
    draft.actions.length > 1
      ? `Confirm ${draft.actions.length} actions`
      : a.type === "transfer"
        ? "Transfer now"
        : a.type === "bill_payment"
          ? "Pay now"
          : "Place order";
  async function send() {
    if (preview) return;
    if (
      locked.current ||
      uncertain ||
      result?.status === "executed" ||
      result?.status === "rejected"
    )
      return;
    locked.current = true;
    setBusy(true);
    onLock(true);
    setError("");
    const controller = new AbortController();
    abort.current = controller;
    let executing = false;
    try {
      pending.current ??= await preparePayment(draft, controller.signal);
      controller.signal.throwIfAborted();
      executing = true;
      const reply = await submitPayment(
        pending.current,
        result?.status === "needs_extra_check" ? otp : null,
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setResult(reply);
        setOtp("");
        if (reply.status !== "needs_extra_check")
          onSettled(reply.status === "executed");
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        setUncertain(executing);
        if (executing) onSettled(false);
        setError(
          executing
            ? "We couldn’t confirm the outcome. Check your account before trying again."
            : e instanceof Error
              ? e.message
              : "Unable to connect to the bank.",
        );
      }
    } finally {
      locked.current = false;
      if (!controller.signal.aborted) {
        setBusy(false);
        onLock(false);
      }
    }
  }
  if (result?.status === "executed")
    return (
      <div className="v5-bank-result" role="status">
        <Check size={22} />
        <h3>Request completed</h3>
        {result.results.map((r) => (
          <p key={r.action_index}>{r.reference}</p>
        ))}
        <button className="v5-primary" onClick={onCancel}>
          Done
        </button>
      </div>
    );
  if (result?.status === "rejected")
    return (
      <div className="v5-bank-result" role="alert">
        <h3>Payment not completed</h3>
        <p>{result.message}</p>
        <button className="v5-secondary" onClick={onCancel}>
          Start again
        </button>
      </div>
    );
  return (
    <div className="v5-payment">
      {error && (
        <p role="alert" className="v5-warning">
          {error}
        </p>
      )}
      {result?.status === "needs_extra_check" && (
        <label className="v5-otp">
          Enter your one-time code
          <input
            aria-label="One-time code"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
            disabled={busy || uncertain}
          />
          <span>{result.message}</span>
        </label>
      )}
      <div className="v5-actions">
        <button className="v5-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          className="v5-primary"
          disabled={
            preview ||
            busy ||
            uncertain ||
            (result?.status === "needs_extra_check" && !otp.trim())
          }
          onClick={() => void send()}
        >
          {busy
            ? "Confirming…"
            : result?.status === "needs_extra_check"
              ? "Confirm code"
              : label}
          {!busy && <LockKeyhole size={14} />}
        </button>
      </div>
    </div>
  );
}
