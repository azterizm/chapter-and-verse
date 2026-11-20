import { afterEach, describe, expect, it, vi } from "vitest";
import { type AskHandlers, type AskRequest, ask } from "../src/api/client";
import { initialState, reducer } from "../src/state/reducer";
import {
  TurnstileGate,
  turnstileOptions,
  TURNSTILE_ACTION,
  TURNSTILE_CHECKING_MESSAGE,
  TURNSTILE_TIMEOUT_MESSAGE,
} from "../src/components/Turnstile";
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

  it("render options include action: \"ask\"", () => {
    expect(TURNSTILE_ACTION).toBe("ask");
    const onToken = vi.fn();
    const onError = vi.fn();
    const opts = turnstileOptions(onToken, onError);
    expect(opts.action).toBe("ask");
    expect(opts.appearance).toBe("interaction-only");

    const renderMock = vi.fn();
    const mockTs = { render: renderMock, reset: vi.fn(), remove: vi.fn() };
    mockTs.render({} as HTMLElement, opts);
    expect(renderMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ action: "ask", appearance: "interaction-only" }),
    );
  });

  it("a question asked before the token arrives is sent once the token arrives, with that token", async () => {
    const sent: Array<{ query: string; token: string | null }> = [];
    const statuses: Array<string | null> = [];
    let resets = 0;

    const gate = new TurnstileGate({
      onSend: (query, token) => sent.push({ query, token }),
      onReset: () => resets++,
      onStatusChange: (s) => statuses.push(s),
      onError: vi.fn(),
    });

    // Ask before token arrives
    gate.ask("s.124 ERA 1996");

    expect(sent).toHaveLength(0);
    expect(gate.isWaiting()).toBe(true);
    expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE]);

    // Token arrives asynchronously
    gate.onToken("fresh-token-xyz");

    expect(sent).toEqual([{ query: "s.124 ERA 1996", token: "fresh-token-xyz" }]);
    expect(gate.isWaiting()).toBe(false);
    expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE, null]);
    expect(resets).toBe(1);

    // Verify it sends with this token to the API
    const fetchCalls: Array<{ url: string; headers: Headers; body: string }> = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      fetchCalls.push({
        url: String(input),
        headers: new Headers(init?.headers),
        body: typeof init?.body === "string" ? init.body : "",
      });
      return new Response("", { status: 200, headers: { "Content-Type": "text/event-stream" } });
    });

    const req: AskRequest = { query: sent[0]!.query };
    if (sent[0]!.token) req.turnstile_token = sent[0]!.token;

    await ask(
      req,
      { onEvent: vi.fn(), onFailure: vi.fn() },
      new AbortController().signal,
    );

    expect(fetchCalls[0]!.headers.get("X-Turnstile-Token")).toBe("fresh-token-xyz");
    expect(JSON.parse(fetchCalls[0]!.body)).toMatchObject({
      query: "s.124 ERA 1996",
      turnstile_token: "fresh-token-xyz",
    });
  });

  it("the 15 s timeout path (fake timers)", () => {
    vi.useFakeTimers();
    try {
      const sent: Array<{ query: string; token: string | null }> = [];
      const errors: string[] = [];
      const statuses: Array<string | null> = [];

      const gate = new TurnstileGate({
        onSend: (query, token) => sent.push({ query, token }),
        onReset: vi.fn(),
        onStatusChange: (s) => statuses.push(s),
        onError: (err) => errors.push(err),
      });

      gate.ask("s.124 ERA 1996");

      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE]);
      expect(gate.isWaiting()).toBe(true);

      // Advance by 14,999 ms: still waiting
      vi.advanceTimersByTime(14999);
      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Advance remaining 1 ms (reaches 15 s)
      vi.advanceTimersByTime(1);
      expect(sent).toHaveLength(0);
      expect(gate.isWaiting()).toBe(false);
      expect(errors).toEqual([TURNSTILE_TIMEOUT_MESSAGE]);
      expect(TURNSTILE_TIMEOUT_MESSAGE).toBe("The bot check did not finish. Reload the page.");
      expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE, null]);

      // Late token arrival after timeout does not send the aborted question
      gate.onToken("late-token");
      expect(sent).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("sends immediately when a token is already ready", () => {
    const sent: Array<{ query: string; token: string | null }> = [];
    let resets = 0;
    const gate = new TurnstileGate({
      onSend: (query, token) => sent.push({ query, token }),
      onReset: () => resets++,
      onStatusChange: vi.fn(),
      onError: vi.fn(),
    });

    gate.onToken("cached-token");
    gate.ask("s.124 ERA 1996");

    expect(sent).toEqual([{ query: "s.124 ERA 1996", token: "cached-token" }]);
    expect(resets).toBe(1);
    expect(gate.isWaiting()).toBe(false);
  });

  it("onFailure stops waiting and reports the error without sending", () => {
    const sent: Array<{ query: string; token: string | null }> = [];
    const errors: string[] = [];
    const gate = new TurnstileGate({
      onSend: (query, token) => sent.push({ query, token }),
      onReset: vi.fn(),
      onStatusChange: vi.fn(),
      onError: (err) => errors.push(err),
    });

    gate.ask("s.124 ERA 1996");
    gate.onFailure("The bot check failed to run. Reload the page.");

    expect(sent).toHaveLength(0);
    expect(errors).toEqual(["The bot check failed to run. Reload the page."]);
    expect(gate.isWaiting()).toBe(false);
  });
});
