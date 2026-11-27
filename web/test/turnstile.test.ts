import { afterEach, describe, expect, it, vi } from "vitest";
import { type AskHandlers, type AskRequest, ask } from "../src/api/client";
import { initialState, reducer } from "../src/state/reducer";
import {
  TurnstileGate,
  turnstileOptions,
  TURNSTILE_ACTION,
  TURNSTILE_CHECKING_MESSAGE,
  TURNSTILE_TIMEOUT_MESSAGE,
  TURNSTILE_TIMEOUT_MS,
  TURNSTILE_UNSUPPORTED_MESSAGE,
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

  it("render options include appearance: \"interaction-only\", execution: \"execute\", and action: \"ask\"", () => {
    expect(TURNSTILE_ACTION).toBe("ask");
    const onToken = vi.fn();
    const onError = vi.fn();
    const opts = turnstileOptions(onToken, onError, undefined, 680, "light");
    expect(opts.action).toBe("ask");
    expect(opts.appearance).toBe("interaction-only");
    expect(opts.execution).toBe("execute");
    expect(opts.size).toBe("flexible");
    expect(opts.theme).toBe("light");

    const renderMock = vi.fn();
    const mockTs = { render: renderMock, reset: vi.fn(), remove: vi.fn() };
    mockTs.render({} as HTMLElement, opts);
    expect(renderMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ action: "ask", appearance: "interaction-only", execution: "execute" }),
    );
  });

  it("uses flexible size when container width is at least 300 px (680) and compact below 300 px (288)", () => {
    const onToken = vi.fn();
    const onError = vi.fn();

    const desktopOpts = turnstileOptions(onToken, onError, undefined, 680, "light");
    expect(desktopOpts.size).toBe("flexible");
    expect(desktopOpts.appearance).toBe("interaction-only");
    expect(desktopOpts.action).toBe("ask");

    const boundaryOpts = turnstileOptions(onToken, onError, undefined, 300, "light");
    expect(boundaryOpts.size).toBe("flexible");

    const narrowOpts = turnstileOptions(onToken, onError, undefined, 288, "light");
    expect(narrowOpts.size).toBe("compact");
    expect(narrowOpts.appearance).toBe("interaction-only");
    expect(narrowOpts.action).toBe("ask");
  });

  it("passes resolved theme through to render options without altering appearance or action", () => {
    const onToken = vi.fn();
    const onError = vi.fn();

    const lightOpts = turnstileOptions(onToken, onError, undefined, 680, "light");
    expect(lightOpts.theme).toBe("light");
    expect(lightOpts.appearance).toBe("interaction-only");
    expect(lightOpts.action).toBe("ask");

    const darkOpts = turnstileOptions(onToken, onError, undefined, 288, "dark");
    expect(darkOpts.theme).toBe("dark");
    expect(darkOpts.size).toBe("compact");
    expect(darkOpts.appearance).toBe("interaction-only");
    expect(darkOpts.action).toBe("ask");
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

  it("token arrives late (25 s) and the question is sent", () => {
    vi.useFakeTimers();
    try {
      expect(TURNSTILE_TIMEOUT_MS).toBe(30000);
      const sent: Array<{ query: string; token: string | null }> = [];
      const errors: string[] = [];
      const statuses: Array<string | null> = [];
      let resets = 0;

      const gate = new TurnstileGate({
        onSend: (query, token) => sent.push({ query, token }),
        onReset: () => resets++,
        onStatusChange: (s) => statuses.push(s),
        onError: (err) => errors.push(err),
      });

      gate.ask("s.124 ERA 1996");

      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE]);
      expect(gate.isWaiting()).toBe(true);

      // Advance by 25 s (25,000 ms)
      vi.advanceTimersByTime(25000);
      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Token arrives at 25 s
      gate.onToken("token-25s");

      expect(sent).toEqual([{ query: "s.124 ERA 1996", token: "token-25s" }]);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(false);
      expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE, null]);
      expect(resets).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("first wait times out, reset, token arrives and the question is sent", () => {
    vi.useFakeTimers();
    try {
      const sent: Array<{ query: string; token: string | null }> = [];
      const errors: string[] = [];
      const statuses: Array<string | null> = [];
      let resets = 0;

      const gate = new TurnstileGate({
        onSend: (query, token) => sent.push({ query, token }),
        onReset: () => resets++,
        onStatusChange: (s) => statuses.push(s),
        onError: (err) => errors.push(err),
      });

      gate.ask("s.124 ERA 1996");

      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Advance by 30 s (first wait times out)
      vi.advanceTimersByTime(30000);

      // Resets widget once and continues waiting
      expect(resets).toBe(1);
      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Advance 15 s into the retry wait (total 45 s)
      vi.advanceTimersByTime(15000);

      // Token arrives during retry
      gate.onToken("token-during-retry");

      expect(sent).toEqual([{ query: "s.124 ERA 1996", token: "token-during-retry" }]);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(false);
      expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE, null]);
      expect(resets).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("both waits time out and the R5 message shows", () => {
    vi.useFakeTimers();
    try {
      const sent: Array<{ query: string; token: string | null }> = [];
      const errors: string[] = [];
      const statuses: Array<string | null> = [];
      let resets = 0;

      const gate = new TurnstileGate({
        onSend: (query, token) => sent.push({ query, token }),
        onReset: () => resets++,
        onStatusChange: (s) => statuses.push(s),
        onError: (err) => errors.push(err),
      });

      gate.ask("s.124 ERA 1996");

      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Advance 30 s (first wait times out -> resets widget once)
      vi.advanceTimersByTime(30000);
      expect(resets).toBe(1);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Advance another 29,999 ms: still waiting during retry
      vi.advanceTimersByTime(29999);
      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Advance remaining 1 ms (reaches 60 s total, second wait times out)
      vi.advanceTimersByTime(1);
      expect(sent).toHaveLength(0);
      expect(gate.isWaiting()).toBe(false);
      expect(errors).toEqual([TURNSTILE_TIMEOUT_MESSAGE]);
      expect(TURNSTILE_TIMEOUT_MESSAGE).toBe(
        "The bot check did not finish. Click Ask again, or reload the page if it keeps failing.",
      );
      expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE, null]);

      // Late token arrival after timeout does not send the aborted question
      gate.onToken("late-token");
      expect(sent).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("clicking Ask again after timeout starts a fresh attempt (R5)", () => {
    vi.useFakeTimers();
    try {
      const sent: Array<{ query: string; token: string | null }> = [];
      const errors: string[] = [];
      const statuses: Array<string | null> = [];
      let resets = 0;

      const gate = new TurnstileGate({
        onSend: (query, token) => sent.push({ query, token }),
        onReset: () => resets++,
        onStatusChange: (s) => statuses.push(s),
        onError: (err) => errors.push(err),
      });

      gate.ask("s.124 ERA 1996");
      vi.advanceTimersByTime(60000);

      expect(errors).toEqual([TURNSTILE_TIMEOUT_MESSAGE]);
      expect(gate.isWaiting()).toBe(false);

      // Clicking Ask again starts a fresh attempt
      gate.ask("s.124 ERA 1996");
      expect(gate.isWaiting()).toBe(true);
      expect(statuses).toEqual([TURNSTILE_CHECKING_MESSAGE, null, TURNSTILE_CHECKING_MESSAGE]);

      // Token arrives on fresh attempt
      gate.onToken("token-fresh");
      expect(sent).toEqual([{ query: "s.124 ERA 1996", token: "token-fresh" }]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("unsupported callback message", () => {
    expect(TURNSTILE_UNSUPPORTED_MESSAGE).toBe(
      "This browser cannot run the bot check. Try another browser.",
    );
    const onToken = vi.fn();
    const onError = vi.fn();
    const opts = turnstileOptions(onToken, onError);

    expect(typeof opts["unsupported-callback"]).toBe("function");
    opts["unsupported-callback"]();
    expect(onError).toHaveBeenCalledWith(
      "This browser cannot run the bot check. Try another browser.",
    );
  });

  it("handles timeout-callback with reset and retry within single retry limit", () => {
    vi.useFakeTimers();
    try {
      const sent: Array<{ query: string; token: string | null }> = [];
      const errors: string[] = [];
      let resets = 0;

      const gate = new TurnstileGate({
        onSend: (query, token) => sent.push({ query, token }),
        onReset: () => resets++,
        onStatusChange: vi.fn(),
        onError: (err) => errors.push(err),
      });

      gate.ask("s.124 ERA 1996");

      // Turnstile timeout-callback fires at 10 s
      vi.advanceTimersByTime(10000);
      gate.onTimeout();

      // First timeout resets the widget and retries
      expect(resets).toBe(1);
      expect(sent).toHaveLength(0);
      expect(errors).toHaveLength(0);
      expect(gate.isWaiting()).toBe(true);

      // Second timeout-callback fires: retry limit reached
      gate.onTimeout();
      expect(sent).toHaveLength(0);
      expect(errors).toEqual([TURNSTILE_TIMEOUT_MESSAGE]);
      expect(gate.isWaiting()).toBe(false);
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

  it("executes on demand and stays hidden unless Turnstile requests interaction", () => {
    vi.useFakeTimers();
    try {
      const sent: Array<{ query: string; token: string | null }> = [];
      const visibleStates: boolean[] = [];
      let executes = 0;

      const gate = new TurnstileGate({
        onSend: (query, token) => sent.push({ query, token }),
        onExecute: () => executes++,
        onReset: vi.fn(),
        onStatusChange: vi.fn(),
        onNeedVisible: (v) => visibleStates.push(v),
        onError: vi.fn(),
      });

      // Silent verification stays hidden even if it takes >2s
      gate.ask("first question");
      expect(executes).toBe(1);
      vi.advanceTimersByTime(10000);
      expect(visibleStates).toEqual([]);
      gate.onToken("fast-tok");
      expect(sent).toEqual([{ query: "first question", token: "fast-tok" }]);
      expect(visibleStates).toEqual([false]);

      // Explicit before/after interactive callbacks reveal and hide the corner card
      gate.ask("second question");
      expect(executes).toBe(2);
      expect(visibleStates).toEqual([false]);
      gate.onBeforeInteractive();
      expect(visibleStates).toEqual([false, true]);
      gate.onAfterInteractive();
      expect(visibleStates).toEqual([false, true, false]);
      gate.onToken("interactive-tok");
      expect(sent).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("sends X-Session-Token header without putting sessionToken in JSON body and forwards minted token", async () => {
    const fetchCalls: Array<{ headers: Headers; body: string }> = [];
    globalThis.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      fetchCalls.push({
        headers: new Headers(init?.headers),
        body: typeof init?.body === "string" ? init.body : "",
      });
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              'event: done\ndata: {"type":"done","timings":{"first_source_ms":null,"first_sentence_ms":null,"total_ms":1}}\n\n',
            ),
          );
          controller.close();
        },
      });
      return new Response(stream, {
        status: 200,
        headers: { "Content-Type": "text/event-stream", "X-Session-Token": "v1.minted.sig" },
      });
    });

    const onSessionToken = vi.fn();
    await ask(
      { query: "follow-up", session_id: "sid-1", sessionToken: "v1.existing.sig" },
      { onEvent: vi.fn(), onFailure: vi.fn(), onSessionToken },
      new AbortController().signal,
    );

    expect(fetchCalls).toHaveLength(1);
    expect(fetchCalls[0]!.headers.get("X-Session-Token")).toBe("v1.existing.sig");
    expect(fetchCalls[0]!.headers.get("X-Turnstile-Token")).toBeNull();
    const parsedBody = JSON.parse(fetchCalls[0]!.body) as Record<string, unknown>;
    expect(parsedBody).toEqual({ query: "follow-up", session_id: "sid-1" });
    expect("sessionToken" in parsedBody).toBe(false);
    expect(onSessionToken).toHaveBeenCalledWith("v1.minted.sig");
  });
});
