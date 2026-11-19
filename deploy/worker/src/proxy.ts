/**
 * Forwarding to the origins. The timeout covers connecting and receiving the response headers
 * only; once headers arrive the timer is cleared and the body (SSE included) streams through
 * unbuffered for as long as the origin keeps it open.
 */

import { upstreamUrl, type Origin } from "./origins";

export interface Forward {
  method: string;
  pathname: string;
  search: string;
  headers: Headers;
  body: Uint8Array | null;
}

export type ForwardResult =
  | { ok: true; origin: Origin; response: Response }
  | { ok: false; reason: "timeout" | "unreachable" | "no-origin"; status?: number };

/** Statuses that mean "this origin is down", so the next origin (Slice 4) may be tried. */
const FAILOVER_STATUSES = new Set([502, 503, 504, 530]);

export async function fetchWithTimeout(
  fetchFn: typeof fetch,
  url: string,
  init: RequestInit,
  timeoutMs: number,
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchFn(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

/** Try each origin in order; return the first that answers with a non-failover status. */
export async function forward(
  fetchFn: typeof fetch,
  origins: readonly Origin[],
  req: Forward,
  timeoutMs: number,
): Promise<ForwardResult> {
  if (origins.length === 0) return { ok: false, reason: "no-origin" };
  let last: ForwardResult = { ok: false, reason: "unreachable" };
  for (const [i, origin] of origins.entries()) {
    const isLast = i === origins.length - 1;
    try {
      const response = await fetchWithTimeout(
        fetchFn,
        upstreamUrl(origin, req.pathname, req.search),
        {
          method: req.method,
          headers: req.headers,
          body: req.body,
          redirect: "manual",
        },
        timeoutMs,
      );
      if (!isLast && FAILOVER_STATUSES.has(response.status)) {
        await response.body?.cancel();
        last = { ok: false, reason: "unreachable", status: response.status };
        continue;
      }
      return { ok: true, origin, response };
    } catch (err) {
      const aborted = err instanceof Error && err.name === "AbortError";
      last = { ok: false, reason: aborted ? "timeout" : "unreachable" };
    }
  }
  return last;
}

export interface HealthResult {
  origin: string;
  status: number | null;
  ms: number;
}

/** GET /api/health on every origin (the cron). Never throws. */
export async function pingAll(
  fetchFn: typeof fetch,
  origins: readonly Origin[],
  headers: Record<string, string>,
  timeoutMs: number,
  now: () => number = Date.now,
): Promise<HealthResult[]> {
  return Promise.all(
    origins.map(async (origin) => {
      const start = now();
      try {
        const resp = await fetchWithTimeout(
          fetchFn,
          upstreamUrl(origin, "/api/health", ""),
          { method: "GET", headers: { Accept: "application/json", ...headers } },
          timeoutMs,
        );
        await resp.body?.cancel();
        return { origin: origin.name, status: resp.status, ms: now() - start };
      } catch {
        return { origin: origin.name, status: null, ms: now() - start };
      }
    }),
  );
}
