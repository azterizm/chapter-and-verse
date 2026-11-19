import { describe, expect, it, vi } from "vitest";

import worker, { handle, healthCron, type Deps, type Env } from "../src/index";
import { API_LIMIT, ASK_LIMIT, TokenBucket } from "../src/ratelimit";
import { SITEVERIFY_URL } from "../src/turnstile";

const SITE = "https://site.example";
const ENV: Env = {
  ORIGIN_URL: "https://origin.example",
  ALLOWED_ORIGINS: SITE,
  ORIGIN_TIMEOUT_MS: "1000",
  MAX_BODY_BYTES: "12288",
  TURNSTILE_HOSTNAMES: "site.example",
  TURNSTILE_ACTION: "ask",
  TURNSTILE_SECRET: "turnstile-secret",
  SESSION_SIGNING_KEY: "s".repeat(48),
  ACCESS_CLIENT_ID: "access-id.access",
  ACCESS_CLIENT_SECRET: "access-secret-value-0123456789",
};

type Call = { url: string; init: RequestInit };

function makeDeps(origin: (url: string, init: RequestInit) => Promise<Response>, siteverifyOk = true) {
  const calls: Call[] = [];
  const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init: init ?? {} });
    if (url === SITEVERIFY_URL) {
      return Response.json({ success: siteverifyOk, hostname: "site.example", action: "ask" });
    }
    return origin(url, init ?? {});
  });
  const deps: Deps = {
    fetch: fetchFn as unknown as typeof fetch,
    now: () => 1_700_000_000_000,
    apiBucket: new TokenBucket(API_LIMIT),
    askBucket: new TokenBucket(ASK_LIMIT),
  };
  return { deps, calls };
}

const okOrigin = async () =>
  new Response('{"status":"ok"}', {
    headers: { "Content-Type": "application/json", "Set-Cookie": "x=1", Server: "uvicorn", "Access-Control-Allow-Origin": "*" },
  });

function ask(headers: Record<string, string> = {}, body = '{"query":"s.124 ERA 1996"}', ip = "203.0.113.9"): Request {
  return new Request(`${SITE}/api/ask`, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/json", Origin: SITE, "CF-Connecting-IP": ip, ...headers },
  });
}

describe("routing and CORS", () => {
  it("404s outside /api/", async () => {
    const { deps } = makeDeps(okOrigin);
    expect((await handle(new Request(`${SITE}/`), ENV, deps)).status).toBe(404);
  });
  it("answers preflight for the allowed origin only", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const pre = (origin: string) =>
      handle(new Request(`${SITE}/api/ask`, { method: "OPTIONS", headers: { Origin: origin } }), ENV, deps);
    const good = await pre(SITE);
    expect(good.status).toBe(204);
    expect(good.headers.get("Access-Control-Allow-Origin")).toBe(SITE);
    expect(good.headers.get("Access-Control-Allow-Headers")).toContain("X-Turnstile-Token");
    expect((await pre("https://evil.example")).status).toBe(403);
    expect(calls).toHaveLength(0);
  });
  it("rejects other methods", async () => {
    const { deps } = makeDeps(okOrigin);
    const r = await handle(new Request(`${SITE}/api/ask`, { method: "PUT", body: "{}" }), ENV, deps);
    expect(r.status).toBe(405);
  });
  it("proxies GET /api/health with security headers and without origin cookies", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const r = await handle(new Request(`${SITE}/api/health`, { headers: { Origin: SITE, Cookie: "c=1" } }), ENV, deps);
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ status: "ok" });
    expect(r.headers.get("Set-Cookie")).toBeNull();
    expect(r.headers.get("Server")).toBeNull();
    expect(r.headers.get("Access-Control-Allow-Origin")).toBe(SITE);
    expect(r.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(r.headers.get("Cache-Control")).toBe("no-store");
    expect(calls[0]?.url).toBe("https://origin.example/api/health");
    const sent = calls[0]?.init.headers as Headers;
    expect(sent.get("Cookie")).toBeNull();
    expect(sent.get("CF-Access-Client-Id")).toBe("access-id.access");
  });
});

describe("questions", () => {
  it("requires Turnstile on the first question", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const r = await handle(ask(), ENV, deps);
    expect(r.status).toBe(401);
    expect(((await r.json()) as { error: string }).error).toBe("turnstile_required");
    expect(calls).toHaveLength(0);
  });
  it("refuses a failed Turnstile check", async () => {
    const { deps, calls } = makeDeps(okOrigin, false);
    const r = await handle(ask({ "X-Turnstile-Token": "bad" }), ENV, deps);
    expect(r.status).toBe(403);
    expect(calls.map((c) => c.url)).toEqual([SITEVERIFY_URL]);
  });
  it("starts a session, then reuses it without Turnstile", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const first = await handle(ask({ "X-Turnstile-Token": "good" }), ENV, deps);
    expect(first.status).toBe(200);
    const token = first.headers.get("X-Session-Token");
    expect(token).toMatch(/^v1\./);
    expect(first.headers.get("Access-Control-Expose-Headers")).toContain("X-Session-Token");
    const upstream = calls[1]!;
    expect(upstream.url).toBe("https://origin.example/api/ask");
    const sid = (upstream.init.headers as Headers).get("X-CV-Session-Id");
    expect(sid).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(new TextDecoder().decode(upstream.init.body as Uint8Array)).toBe('{"query":"s.124 ERA 1996"}');

    calls.length = 0;
    const second = await handle(ask({ "X-Session-Token": token! }), ENV, deps);
    expect(second.status).toBe(200);
    expect(second.headers.get("X-Session-Token")).toBeNull();
    expect(calls.map((c) => c.url)).toEqual(["https://origin.example/api/ask"]);
    expect((calls[0]!.init.headers as Headers).get("X-CV-Session-Id")).toBe(sid);
  });
  it("drops a client-sent session id header", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    await handle(ask({ "X-Turnstile-Token": "good", "X-CV-Session-Id": "forged" }), ENV, deps);
    expect((calls[1]!.init.headers as Headers).get("X-CV-Session-Id")).not.toBe("forged");
  });
  it("needs a session for other POSTs", async () => {
    const { deps } = makeDeps(okOrigin);
    const r = await handle(
      new Request(`${SITE}/api/feedback`, {
        method: "POST",
        body: "{}",
        headers: { "Content-Type": "application/json", Origin: SITE, "X-Turnstile-Token": "good" },
      }),
      ENV,
      deps,
    );
    expect(r.status).toBe(401);
  });
  it("caps the body at 12288 bytes and needs JSON", async () => {
    const { deps } = makeDeps(okOrigin);
    const body = (n: number) => JSON.stringify({ query: "x".repeat(n - 12) });
    expect(body(12288).length).toBe(12288);
    expect((await handle(ask({ "X-Turnstile-Token": "good" }, body(12288)), ENV, deps)).status).toBe(200);
    expect((await handle(ask({ "X-Turnstile-Token": "good" }, body(12289)), ENV, deps)).status).toBe(413);
    expect((await handle(ask({ "Content-Type": "text/plain" }), ENV, deps)).status).toBe(415);
  });
  it("rejects a foreign Origin", async () => {
    const { deps } = makeDeps(okOrigin);
    expect((await handle(ask({ Origin: "https://evil.example" }), ENV, deps)).status).toBe(403);
  });
  it("fails closed without secrets", async () => {
    const { deps } = makeDeps(okOrigin);
    const env = { ...ENV, TURNSTILE_SECRET: undefined };
    expect((await handle(ask({ "X-Turnstile-Token": "good" }), env, deps)).status).toBe(503);
  });
  it("limits questions per IP and tells the client when to retry", async () => {
    const { deps } = makeDeps(okOrigin);
    const statuses: number[] = [];
    for (let i = 0; i < ASK_LIMIT.limit + 1; i++) {
      statuses.push((await handle(ask({ "X-Turnstile-Token": "good" }), ENV, deps)).status);
    }
    expect(statuses.slice(0, ASK_LIMIT.limit).every((s) => s === 200)).toBe(true);
    const last = await handle(ask({ "X-Turnstile-Token": "good" }), ENV, deps);
    expect(last.status).toBe(429);
    expect(last.headers.get("Retry-After")).toBe("60");
    // Another IP is unaffected.
    expect((await handle(ask({ "X-Turnstile-Token": "good" }, undefined, "198.51.100.7"), ENV, deps)).status).toBe(200);
  });
  it("uses the Rate Limiting binding when bound", async () => {
    const { deps } = makeDeps(okOrigin);
    const env = { ...ENV, API_LIMITER: { limit: vi.fn().mockResolvedValue({ success: false }) } };
    expect((await handle(new Request(`${SITE}/api/health`), env, deps)).status).toBe(429);
    expect(env.API_LIMITER.limit).toHaveBeenCalledWith({ key: "unknown" });
  });
});

describe("origin behaviour", () => {
  it("streams SSE through before the origin finishes", async () => {
    let finish!: () => void;
    const done = new Promise<void>((r) => (finish = r));
    const enc = new TextEncoder();
    const sse = async () =>
      new Response(
        new ReadableStream<Uint8Array>({
          async start(c) {
            c.enqueue(enc.encode("event: route\ndata: {}\n\n"));
            await done;
            c.enqueue(enc.encode("event: done\ndata: {}\n\n"));
            c.close();
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const { deps } = makeDeps(sse);
    const r = await handle(ask({ "X-Turnstile-Token": "good" }), ENV, deps);
    expect(r.headers.get("Cache-Control")).toBe("no-cache, no-transform");
    expect(r.headers.get("Content-Type")).toBe("text/event-stream");
    const reader = r.body!.getReader();
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toContain("event: route");
    finish();
    const rest = await reader.read();
    expect(new TextDecoder().decode(rest.value)).toContain("event: done");
  });
  it("returns 504 when the origin does not answer in time", async () => {
    const hang = (_u: string, init: RequestInit) =>
      new Promise<Response>((_, reject) =>
        init.signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }))),
      );
    const { deps } = makeDeps(hang);
    const r = await handle(new Request(`${SITE}/api/health`), { ...ENV, ORIGIN_TIMEOUT_MS: "500" }, deps);
    expect(r.status).toBe(504);
  });
  it("returns 502 when the origin is unreachable", async () => {
    const { deps } = makeDeps(async () => {
      throw new TypeError("connect failed");
    });
    expect((await handle(new Request(`${SITE}/api/health`), ENV, deps)).status).toBe(502);
  });
  it("returns 503 when no origin is configured", async () => {
    const { deps } = makeDeps(okOrigin);
    expect((await handle(new Request(`${SITE}/api/health`), { ...ENV, ORIGIN_URL: "http://insecure" }, deps)).status).toBe(503);
  });
});

describe("cron", () => {
  it("pings /api/health with the Access headers and logs the result", async () => {
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const f = vi.fn(async () => new Response("{}"));
    await healthCron(ENV, f as unknown as typeof fetch);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://origin.example/api/health");
    expect((init.headers as Record<string, string>)["CF-Access-Client-Id"]).toBe("access-id.access");
    expect((init.headers as Record<string, string>)["CF-Access-Client-Secret"]).toBe(ENV.ACCESS_CLIENT_SECRET);
    expect(log.mock.calls[0]?.[0]).toContain('"event":"health"');
    log.mockRestore();
  });
  it("exports fetch and scheduled handlers", () => {
    expect(typeof worker.fetch).toBe("function");
    expect(typeof worker.scheduled).toBe("function");
  });
});

describe("Access service token", () => {
  const secret = ENV.ACCESS_CLIENT_SECRET!;
  const echoOrigin = async (_u: string, init: RequestInit) => {
    const h = init.headers as Headers;
    // A misbehaving origin that reflects the token back.
    return new Response(`{"echo":"${h.get("CF-Access-Client-Secret")}"}`.replace(secret, "redacted"), {
      headers: {
        "Content-Type": "application/json",
        "CF-Access-Client-Id": h.get("CF-Access-Client-Id") ?? "",
        "CF-Access-Client-Secret": h.get("CF-Access-Client-Secret") ?? "",
        "Cf-Access-Authenticated-User-Email": "x@example",
      },
    });
  };

  it("is attached to every origin request and never echoed or logged", async () => {
    const lines: string[] = [];
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation((...args: unknown[]) => void lines.push(args.map(String).join(" "))),
    );
    const { deps, calls } = makeDeps(echoOrigin);
    const responses = [
      await handle(new Request(`${SITE}/api/health`, { headers: { Origin: SITE } }), ENV, deps),
      await handle(ask({ "X-Turnstile-Token": "good" }), ENV, deps),
    ];
    // Failure paths that log: a failed Turnstile check, and the cron with an unreachable origin.
    const failing = makeDeps(echoOrigin, false);
    responses.push(await handle(ask({ "X-Turnstile-Token": "bad" }), ENV, failing.deps));
    await healthCron(ENV, (async () => {
      throw new TypeError("down");
    }) as unknown as typeof fetch);

    const originCalls = calls.filter((c) => c.url.startsWith("https://origin.example/"));
    expect(originCalls).toHaveLength(2);
    for (const c of originCalls) {
      const h = c.init.headers as Headers;
      expect(h.get("CF-Access-Client-Id")).toBe(ENV.ACCESS_CLIENT_ID);
      expect(h.get("CF-Access-Client-Secret")).toBe(secret);
    }
    const siteverify = calls.filter((c) => c.url === SITEVERIFY_URL);
    for (const c of siteverify) expect(JSON.stringify([...(c.init.body as FormData).entries()])).not.toContain(secret);

    for (const r of responses) {
      for (const [name, value] of r.headers) {
        expect(name.toLowerCase().startsWith("cf-access-")).toBe(false);
        expect(value).not.toContain(secret);
      }
      expect(await r.text()).not.toContain(secret);
    }
    expect(lines.length).toBeGreaterThan(0);
    for (const line of lines) {
      expect(line).not.toContain(secret);
      expect(line).not.toContain(ENV.ACCESS_CLIENT_ID!);
    }
    for (const s of spies) s.mockRestore();
  });

  it("fails closed when the token is not configured", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const { deps, calls } = makeDeps(okOrigin);
    for (const env of [{ ...ENV, ACCESS_CLIENT_ID: undefined }, { ...ENV, ACCESS_CLIENT_SECRET: " " }]) {
      expect((await handle(new Request(`${SITE}/api/health`), env, deps)).status).toBe(503);
      expect((await handle(ask({ "X-Turnstile-Token": "good" }), env, deps)).status).toBe(503);
    }
    const f = vi.fn();
    await healthCron({ ...ENV, ACCESS_CLIENT_SECRET: undefined }, f as unknown as typeof fetch);
    expect(f).not.toHaveBeenCalled();
    expect(calls).toHaveLength(0);
    err.mockRestore();
  });

  it("drops client-sent Access headers", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    await handle(
      new Request(`${SITE}/api/health`, { headers: { "CF-Access-Client-Id": "forged", "CF-Access-Client-Secret": "forged" } }),
      ENV,
      deps,
    );
    const h = calls[0]!.init.headers as Headers;
    expect(h.get("CF-Access-Client-Id")).toBe(ENV.ACCESS_CLIENT_ID);
    expect(h.get("CF-Access-Client-Secret")).toBe(secret);
  });
});
