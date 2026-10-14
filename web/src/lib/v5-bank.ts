import { z } from "zod";
import { post } from "./api";
import { draftSchema, validationSchema, type Draft, type Validation } from "./contracts";

const challengeSchema = z.strictObject({
  nonce: z.string().min(1),
  signing_payload: z.string().min(1),
  expires_at: z.iso.datetime({ offset: true }),
});
export const executionSchema = z.discriminatedUnion("status", [
  z.strictObject({
    status: z.literal("executed"),
    results: z
      .array(z.strictObject({ action_index: z.number().int().nonnegative(), reference: z.string().min(1) }))
      .min(1),
  }),
  z.strictObject({ status: z.literal("needs_extra_check"), method: z.literal("otp"), message: z.string() }),
  z.strictObject({ status: z.literal("rejected"), reason: z.string(), message: z.string() }),
]);
export type Execution = z.infer<typeof executionSchema>;
export type Payment = { nonce: string; signature: string; expiresAt: number; count: number };

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b))
    return false;
  const left = Object.entries(a),
    right = Object.entries(b);
  return (
    left.length === right.length &&
    left.every(([key, value]) => Object.hasOwn(b, key) && same(value, (b as Record<string, unknown>)[key]))
  );
}
export function checkChallenge(value: unknown, draft: Draft) {
  const challenge = challengeSchema.parse(value);
  if (Date.parse(challenge.expires_at) <= Date.now())
    throw Error("The bank’s confirmation has expired. Please try again later.");
  const suffix = `.${challenge.nonce}`;
  let signed: unknown;
  try {
    signed = JSON.parse(challenge.signing_payload.slice(0, -suffix.length));
  } catch {
    throw Error("The bank is not ready to confirm this payment.");
  }
  if (!challenge.signing_payload.endsWith(suffix) || !same(signed, draft))
    throw Error("The bank returned different payment details. Nothing was signed.");
  return challenge;
}
async function deviceKey(user: string): Promise<CryptoKeyPair> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open("dcta-device", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("keys");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(Error("Could not open your device key."));
  });
  try {
    const stored = await new Promise<CryptoKeyPair | undefined>((resolve, reject) => {
      const req = db.transaction("keys").objectStore("keys").get(user);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(Error("Could not read your device key."));
    });
    if (stored) return stored;
    const key = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, [
      "sign",
      "verify",
    ]);
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("keys", "readwrite");
      tx.objectStore("keys").put(key, user);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(Error("Could not save your device key."));
      tx.onabort = () => reject(Error("Could not save your device key."));
    });
    return key;
  } finally {
    db.close();
  }
}
export async function preparePayment(value: Draft, signal: AbortSignal, approved?: Extract<Validation, {status: "match"}>): Promise<Payment> {
  const draft = draftSchema.parse(value);
  const validation = approved ?? await post("/validate", validationSchema, { draft }, signal);
  if (validation.status !== "match") {
    throw Error("message" in validation ? validation.message : "These details need correction before confirming.");
  }
  // Serialize key creation/registration across tabs sharing the demo user.
  return navigator.locks.request(`dcta-device-${draft.user_id}`, { signal }, async () => {
    const key = await deviceKey(draft.user_id);
    signal.throwIfAborted();
    const public_key_jwk = await crypto.subtle.exportKey("jwk", key.publicKey);
    await post(
      "/device/register",
      z.strictObject({ status: z.literal("registered") }),
      { user_id: draft.user_id, public_key_jwk },
      signal,
    );
    const reply = await post("/challenge", challengeSchema, { draft, receipt: validation.receipt }, signal);
    const challenge = checkChallenge(reply, draft);
    signal.throwIfAborted();
    // Sign the exact server string, never a client-serialized draft.
    const raw = await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" },
      key.privateKey,
      new TextEncoder().encode(challenge.signing_payload),
    );
    const signature = btoa(String.fromCharCode(...new Uint8Array(raw)))
      .replaceAll("+", "-")
      .replaceAll("/", "_")
      .replace(/=+$/, "");
    return {
      nonce: challenge.nonce,
      signature,
      expiresAt: Date.parse(challenge.expires_at),
      count: draft.actions.length,
    };
  });
}
export async function submitPayment(payment: Payment, otp: string | null, signal: AbortSignal) {
  if (Date.now() >= payment.expiresAt) throw Error("Your confirmation expired. Start a new request.");
  const result = await post(
    "/execute",
    executionSchema,
    { nonce: payment.nonce, signature: payment.signature, otp },
    signal,
  );
  if (
    result.status === "executed" &&
    (result.results.length !== payment.count ||
      new Set(result.results.map((r) => r.action_index)).size !== payment.count ||
      result.results.some((r) => r.action_index >= payment.count))
  ) {
    throw Error("The bank returned an incomplete result. Check your account before trying again.");
  }
  return result;
}
