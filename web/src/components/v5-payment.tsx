"use client";
import { useEffect, useRef, useState } from "react";
import { Check, Fingerprint, LockKeyhole } from "lucide-react";
import type { Draft, Validation } from "@/lib/contracts";
import {
  preparePayment,
  submitPayment,
  type Payment,
  type Execution,
} from "@/lib/v5-bank";

export function V5Payment({
  draft,
  validation,
  onCancel,
  onLock,
  onSettled,
  preview = false,
  requireOtp = false,
}: {
  preview?: boolean;
  requireOtp?: boolean; // D-054: unsure voice; the bank asks for the one-time code
  draft: Draft;
  validation: Validation;
  onCancel: () => void;
  onLock: (locked: boolean) => void;
  onSettled: (completed: boolean) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [result, setResult] = useState<Execution | null>(null),
    [otp, setOtp] = useState("");
  const [fingerprintConfirmed, setFingerprintConfirmed] = useState(false);
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
          : a.type === "account_transfer"
            ? "Move now"
            : a.type === "fx_conversion"
              ? "Convert now"
              : "Place order";
  const needsFingerprint =
    result?.status === "needs_extra_check" && result.method === "fingerprint";
  const needsOtp =
    result?.status === "needs_extra_check" && result.method === "otp";
  async function send() {
    if (preview || validation.status !== "match") return;
    if (
      locked.current ||
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
    let unresolved = false;
    try {
      pending.current ??= await preparePayment(
        draft,
        controller.signal,
        validation,
        requireOtp,
      );
      controller.signal.throwIfAborted();
      executing = true;
      const reply = await submitPayment(
        pending.current,
        needsOtp ? otp : null,
        controller.signal,
        needsFingerprint ? fingerprintConfirmed : null,
      );
      if (!controller.signal.aborted) {
        setResult(reply);
        setUncertain(false);
        setOtp("");
        setFingerprintConfirmed(false);
        if (reply.status !== "needs_extra_check")
          onSettled(reply.status === "executed");
      }
    } catch (e) {
      if (!controller.signal.aborted) {
        unresolved = executing;
        setUncertain(executing);
        if (executing) onSettled(false);
        setError(
          executing
            ? "Outcome not confirmed. Check the status of this payment."
            : e instanceof Error
              ? e.message
              : "Unable to connect to the bank.",
        );
      }
    } finally {
      locked.current = false;
      if (!controller.signal.aborted) {
        setBusy(false);
        onLock(unresolved);
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
      {needsOtp && (
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
      {needsFingerprint && (
        <div
          className="v5-fingerprint-prompt"
          role="alertdialog"
          aria-label="Fingerprint verification"
        >
          <p>{result.message}</p>
          <button
            type="button"
            className={
              "v5-fingerprint-btn" +
              (fingerprintConfirmed ? " v5-fingerprint-done" : "")
            }
            aria-label={
              fingerprintConfirmed
                ? "Fingerprint confirmed"
                : "Confirm with fingerprint"
            }
            disabled={busy || uncertain || fingerprintConfirmed}
            onClick={() => setFingerprintConfirmed(true)}
          >
            <Fingerprint size={28} />
            {fingerprintConfirmed ? "Confirmed" : "Tap to confirm"}
          </button>
        </div>
      )}
      <div className="v5-actions">
        <button
          className="v5-secondary"
          onClick={onCancel}
          disabled={busy || uncertain}
        >
          Cancel
        </button>
        <button
          className="v5-primary"
          disabled={
            preview ||
            validation.status !== "match" ||
            busy ||
            (needsOtp && !otp.trim()) ||
            (needsFingerprint && !fingerprintConfirmed)
          }
          onClick={() => void send()}
        >
          {busy
            ? "Confirming…"
            : uncertain
              ? "Check status"
              : needsFingerprint
                ? "Confirm fingerprint"
                : needsOtp
                  ? "Confirm code"
                  : label}
          {!busy && <LockKeyhole size={14} />}
        </button>
      </div>
    </div>
  );
}
