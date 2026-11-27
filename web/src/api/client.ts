import type { Coordinate, CvEvent } from "./events";
import { readEvents, StreamFault, type StreamItem } from "./sse";
import { VERIFICATION_FAILED_MESSAGE } from "../turnstileText";

/** API origin. Empty means same origin (the Worker serves /api/*). */
export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");

export const MAX_QUERY_BYTES = 4096;

export interface AskRequest {
  query: string;
  as_at?: string;
  session_id?: string;
  selected_coordinate?: Coordinate;
  turnstile_token?: string;
  sessionToken?: string | null;
}

export interface ModelStatus {
  ready: boolean;
  warming: boolean;
  warming_for_s: number | null;
  estimated_ready_s: number | null;
}

export interface Health {
  status: string;
  corpus_id: string;
  snapshot: string;
  model?: ModelStatus;
}

export type AskFailure =
  | { kind: "fault"; code: StreamFault["code"]; message: string }
  | { kind: "http"; status: number; message: string }
  | { kind: "network"; message: string }
  | { kind: "aborted" };

export interface AskHandlers {
  onEvent(event: CvEvent, raw: string): void;
  onFailure(failure: AskFailure): void;
  onSessionToken?(token: string): void;
}

export const byteLength = (s: string): number => new TextEncoder().encode(s).length;

/** POST /api/ask and feed every checked event to the handlers, in order. */
export async function ask(req: AskRequest, handlers: AskHandlers, signal: AbortSignal): Promise<void> {
  let res: Response;
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json", Accept: "text/event-stream" };
    if (req.turnstile_token) {
      headers["X-Turnstile-Token"] = req.turnstile_token;
    }
    if (req.sessionToken) {
      headers["X-Session-Token"] = req.sessionToken;
    }
    const payload: Record<string, string> = { query: req.query };
    if (req.as_at !== undefined) payload.as_at = req.as_at;
    if (req.session_id !== undefined) payload.session_id = req.session_id;
    if (req.selected_coordinate !== undefined) payload.selected_coordinate = req.selected_coordinate;
    if (req.turnstile_token !== undefined) payload.turnstile_token = req.turnstile_token;
    res = await fetch(`${API_BASE}/api/ask`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal,
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
  } catch (e) {
    handlers.onFailure(signal.aborted ? { kind: "aborted" } : { kind: "network", message: networkMessage(e) });
    return;
  }

  const mintedSession = res.headers.get("X-Session-Token");
  if (mintedSession) {
    handlers.onSessionToken?.(mintedSession);
  }

  const type = res.headers.get("Content-Type") ?? "";
  if (!type.startsWith("text/event-stream") || res.body === null) {
    // An edge error (Worker, proxy) that never reached the engine's event stream.
    handlers.onFailure({ kind: "http", status: res.status, message: httpMessage(res.status) });
    return;
  }

  try {
    for await (const item of readEvents(res.body) as AsyncGenerator<StreamItem>) {
      handlers.onEvent(item.event, item.raw);
    }
  } catch (e) {
    if (signal.aborted) handlers.onFailure({ kind: "aborted" });
    else if (e instanceof StreamFault) handlers.onFailure({ kind: "fault", code: e.code, message: e.message });
    else handlers.onFailure({ kind: "network", message: networkMessage(e) });
  }
}

export async function health(signal?: AbortSignal): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/api/health`, {
      signal: signal ?? null,
      credentials: "omit",
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body: unknown = await res.json();
    if (typeof body !== "object" || body === null) return null;
    const h = body as Record<string, unknown>;
    if (typeof h.status !== "string" || typeof h.snapshot !== "string") return null;
    let model: ModelStatus | undefined;
    if (typeof h.model === "object" && h.model !== null) {
      const m = h.model as Record<string, unknown>;
      if (typeof m.ready === "boolean" && typeof m.warming === "boolean") {
        model = {
          ready: m.ready,
          warming: m.warming,
          warming_for_s: typeof m.warming_for_s === "number" ? m.warming_for_s : null,
          estimated_ready_s: typeof m.estimated_ready_s === "number" ? m.estimated_ready_s : null,
        };
      }
    }
    return {
      status: h.status,
      snapshot: h.snapshot,
      corpus_id: String(h.corpus_id ?? ""),
      ...(model !== undefined ? { model } : {}),
    };
  } catch {
    return null;
  }
}

function networkMessage(e: unknown): string {
  return e instanceof Error && e.message ? `The connection failed (${e.message}).` : "The connection failed.";
}

function httpMessage(status: number): string {
  if (status === 429) return "Too many questions from this address. Wait a minute and try again.";
  if (status === 413) return "The question is too long.";
  if (status === 403) return VERIFICATION_FAILED_MESSAGE;
  if (status >= 500) return `The server failed (HTTP ${status}). Try again shortly.`;
  return `The server refused the request (HTTP ${status}).`;
}

// ---------------------------------------------------------------------------- receipt bytes

const FACT_PREFIX = ',"fact":';
const RECEIPT_SUFFIX = ',"type":"receipt"}';

/**
 * The fact object's JSON exactly as the engine serialised it, so that sha256 of the file
 * equals answer_id. The receipt data line is canonical JSON with sorted keys:
 * {"answer_id":...,"fact":{...},"type":"receipt"}. Re-serialising in the browser would not
 * do: Python writes 412.0 where JavaScript writes 412. Returns null when the line is not in
 * that form; the caller then falls back to JSON.stringify.
 */
export function factBytesFromRaw(raw: string): string | null {
  if (!raw.startsWith('{"answer_id":') || !raw.endsWith(RECEIPT_SUFFIX)) return null;
  const at = raw.indexOf(FACT_PREFIX);
  if (at === -1) return null;
  const fact = raw.slice(at + FACT_PREFIX.length, raw.length - RECEIPT_SUFFIX.length);
  try {
    JSON.parse(fact);
  } catch {
    return null;
  }
  return fact;
}

export async function sha256Hex(text: string): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** A random session id, held in memory only. */
export function newSessionId(): string {
  const c = globalThis.crypto;
  if (typeof c.randomUUID === "function") return c.randomUUID();
  const b = c.getRandomValues(new Uint8Array(16));
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
