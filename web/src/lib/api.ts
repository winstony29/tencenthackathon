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

export function post<T>(path: string, schema: z.ZodType<T>, body: unknown, signal: AbortSignal) {
  return request(path, schema, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}
