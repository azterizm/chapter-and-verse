import { afterEach, describe, expect, it, vi } from "vitest";
import { type AskHandlers, type AskRequest, ask } from "../src/api/client";
import { initialState, reducer } from "../src/state/reducer";
import { VERIFICATION_FAILED_MESSAGE } from "../src/turnstileText";

describe("Turnstile on every question", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("sends a new token for each question via X-Turnstile-Token header and body", async () => {
    const fetchCalls: Array<{ url: string; headers: Headers; body: string }> = [];

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const headers = new Headers(init?.headers);
      const body = typeof init?.body === "string" ? init.body : "";
      fetchCalls.push({ url, headers, body });

      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              'event: route\ndata: {"type":"route","status":"ROUTE_BOUNDED","next_action":"synthesize","coordinates":[],"corrections":[],"messages":[],"index_snapshot":"2026-09-28"}\n\n',
            ),
          );
          controller.enqueue(
            new TextEncoder().encode(
              'event: done\ndata: {"type":"done","timings":{"first_source_ms":null,"first_sentence_ms":null,"total_ms":10}}\n\n',
            ),
          );
          controller.close();
        },
      });

      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      });
    });

    const handlers: AskHandlers = {
      onEvent: vi.fn(),
      onFailure: vi.fn(),
    };

    // First question with token-1
    const req1: AskRequest = {
      query: "s.124 ERA 1996",
      session_id: "session-1",
      turnstile_token: "token-1",
    };
    await ask(req1, handlers, new AbortController().signal);

    expect(fetchCalls).toHaveLength(1);
    expect(fetchCalls[0]!.headers.get("X-Turnstile-Token")).toBe("token-1");
    expect(JSON.parse(fetchCalls[0]!.body)).toMatchObject({
      query: "s.124 ERA 1996",
      session_id: "session-1",
      turnstile_token: "token-1",
    });

    // Second question with a new token (token-2)
    const req2: AskRequest = {
      query: "s.125 ERA 1996",
      session_id: "session-1",
      turnstile_token: "token-2",
    };
    await ask(req2, handlers, new AbortController().signal);

    expect(fetchCalls).toHaveLength(2);
    expect(fetchCalls[1]!.headers.get("X-Turnstile-Token")).toBe("token-2");
    expect(JSON.parse(fetchCalls[1]!.body)).toMatchObject({
      query: "s.125 ERA 1996",
      session_id: "session-1",
      turnstile_token: "token-2",
    });
  });

  it("handles verification failure with plain message 'Verification failed. Try again.'", async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify({ error: "turnstile_failed" }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      });
    });

    const failures: Parameters<AskHandlers["onFailure"]>[0][] = [];
    const handlers: AskHandlers = {
      onEvent: vi.fn(),
      onFailure: (f) => failures.push(f),
    };

    const req: AskRequest = {
      query: "test query",
      turnstile_token: "bad-token",
    };

    await ask(req, handlers, new AbortController().signal);

    expect(failures).toHaveLength(1);
    expect(failures[0]).toEqual({
      kind: "http",
      status: 403,
      message: VERIFICATION_FAILED_MESSAGE,
    });
    expect(VERIFICATION_FAILED_MESSAGE).toBe("Verification failed. Try again.");

    // Check reducer handles the 403 failure
    let state = reducer(initialState, { type: "start", turnId: 1, query: "test query" });
    state = reducer(state, { type: "failure", turnId: 1, failure: failures[0]! });
    expect(state.turns[0]?.phase).toBe("failed");
    expect(state.turns[0]?.error?.message).toBe("Verification failed. Try again.");
  });

  it("omits X-Turnstile-Token header when token is not provided", async () => {
    const fetchCalls: Array<{ headers: Headers }> = [];
    globalThis.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      fetchCalls.push({ headers: new Headers(init?.headers) });
      return new Response("", { status: 403, headers: { "Content-Type": "application/json" } });
    });

    const handlers: AskHandlers = { onEvent: vi.fn(), onFailure: vi.fn() };
    await ask({ query: "no token" }, handlers, new AbortController().signal);

    expect(fetchCalls[0]!.headers.get("X-Turnstile-Token")).toBeNull();
  });
});
