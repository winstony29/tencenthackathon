import { z } from "zod";

export async function request<T>(path: string, schema: z.ZodType<T>, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    signal: options.signal
      ? AbortSignal.any([options.signal, AbortSignal.timeout(path === "/validate" ? 60000 : 15000)])
      : AbortSignal.timeout(path === "/validate" ? 60000 : 15000),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.error?.message ?? `Request failed (${response.status}). Please try again.`);
  }
  const parsed = schema.safeParse(await response.json());
  if (!parsed.success) throw new Error("The server returned an unexpected response. Please try again.");
  return parsed.data;
}

export function post<T>(path: string, schema: z.ZodType<T>, body: unknown, signal: AbortSignal, auditSession?: string) {
  return request(path, schema, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(auditSession ? {"X-Audit-Session": auditSession} : {}) },
    body: JSON.stringify(body),
    signal,
  });
}


// Browser-reported composition is evidence of client state, never bank authorization.
export function reportPresentation(sessionId: string, draftId: string | undefined, manifest: unknown, source: string) {
  return fetch("/api/audit/presentation", {
    method: "POST", headers: {"Content-Type":"application/json"}, keepalive: true,
    body: JSON.stringify({session_id: sessionId, draft_id: draftId, manifest,
      source: source === "jev" ? "jev" : "fallback"}),
  }).then(response => {
    if (!response.ok) throw new Error("Presentation evidence could not be saved.");
  });
}
