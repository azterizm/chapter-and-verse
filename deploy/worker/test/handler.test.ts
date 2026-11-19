import { describe, expect, it, vi } from "vitest";

import worker, { handle, healthCron, type Deps, type Env } from "../src/index";
import {
  API_LIMIT,
  ASK_LIMIT,
  HOSTING_ASN_LIMIT,
  HOSTING_IP_LIMIT,
  OTHER_ASN_LIMIT,
  TokenBucket,
} from "../src/ratelimit";
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
  HOSTING_ASN_PER_MINUTE: "6",
  HOSTING_IP_PER_MINUTE: "2",
  OTHER_ASN_PER_MINUTE: "120",
};

type Call = { url: string; init: RequestInit };

function makeDeps(origin: (url: string, init: RequestInit) => Promise<Response>, siteverifyOk = true) {
  const calls: Call[] = [];
  const usedTokens = new Set<string>();
  const fetchFn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init: init ?? {} });
    if (url === SITEVERIFY_URL) {
      if (!siteverifyOk) {
        return Response.json({ success: false, hostname: "site.example", action: "ask", "error-codes": ["invalid-input-response"] });
      }
      const form = init?.body as FormData;
      const tok = form?.get("response")?.toString();
      if (tok && usedTokens.has(tok)) {
        return Response.json({ success: false, hostname: "site.example", action: "ask", "error-codes": ["timeout-or-duplicate"] });
      }
      if (tok) usedTokens.add(tok);
      return Response.json({ success: true, hostname: "site.example", action: "ask" });
    }
    return origin(url, init ?? {});
  });
  const deps: Deps = {
    fetch: fetchFn as unknown as typeof fetch,
    now: () => 1_700_000_000_000,
    apiBucket: new TokenBucket(API_LIMIT),
    askBucket: new TokenBucket(ASK_LIMIT),
    hostingAsnBucket: new TokenBucket(HOSTING_ASN_LIMIT),
    hostingIpBucket: new TokenBucket(HOSTING_IP_LIMIT),
    otherAsnBucket: new TokenBucket(OTHER_ASN_LIMIT),
  };
  return { deps, calls };
}

const okOrigin = async () =>
  new Response('{"status":"ok"}', {
    headers: { "Content-Type": "application/json", "Set-Cookie": "x=1", Server: "uvicorn", "Access-Control-Allow-Origin": "*" },
  });

function ask(
  headers: Record<string, string> = {},
  body = '{"query":"s.124 ERA 1996"}',
  ip = "203.0.113.9",
  cf?: { asn?: number; asOrganization?: string },
): Request {
  const req = new Request(`${SITE}/api/ask`, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/json", Origin: SITE, "CF-Connecting-IP": ip, ...headers },
  });
  if (cf !== undefined) {
    Object.defineProperty(req, "cf", { value: cf, configurable: true });
  }
  return req;
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
  it("rejects a question with missing Turnstile token with 403", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const r = await handle(ask(), ENV, deps);
    expect(r.status).toBe(403);
    expect(((await r.json()) as { error: string }).error).toBe("turnstile_required");
    expect(calls).toHaveLength(0);
  });

  it("refuses a failed Turnstile check with 403", async () => {
    const { deps, calls } = makeDeps(okOrigin, false);
    const r = await handle(ask({ "X-Turnstile-Token": "bad" }), ENV, deps);
    expect(r.status).toBe(403);
    expect(((await r.json()) as { error: string }).error).toBe("turnstile_failed");
    expect(calls.map((c) => c.url)).toEqual([SITEVERIFY_URL]);
  });

  it("rejects consecutive question when Turnstile token is missing", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const first = await handle(ask({ "X-Turnstile-Token": "good-1" }), ENV, deps);
    expect(first.status).toBe(200);
    const sessionToken = first.headers.get("X-Session-Token")!;
    expect(sessionToken).toBeTruthy();

    calls.length = 0;
    const second = await handle(ask({ "X-Session-Token": sessionToken }), ENV, deps);
    expect(second.status).toBe(403);
    expect(((await second.json()) as { error: string }).error).toBe("turnstile_required");
    expect(calls).toHaveLength(0);
  });

  it("rejects consecutive question when Turnstile token is invalid", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const first = await handle(ask({ "X-Turnstile-Token": "good-1" }), ENV, deps);
    expect(first.status).toBe(200);
    const sessionToken = first.headers.get("X-Session-Token")!;

    calls.length = 0;
    // Set fetch to reject siteverify for the invalid token
    const origFetch = deps.fetch;
    deps.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === SITEVERIFY_URL) {
        return Response.json({ success: false, hostname: "site.example", action: "ask", "error-codes": ["invalid-input-response"] });
      }
      return origFetch(input, init);
    }) as unknown as typeof fetch;

    const second = await handle(ask({ "X-Session-Token": sessionToken, "X-Turnstile-Token": "bad-2" }), ENV, deps);
    expect(second.status).toBe(403);
    expect(((await second.json()) as { error: string }).error).toBe("turnstile_failed");
  });

  it("rejects consecutive question when Turnstile token is reused", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const first = await handle(ask({ "X-Turnstile-Token": "good-1" }), ENV, deps);
    expect(first.status).toBe(200);
    const sessionToken = first.headers.get("X-Session-Token")!;

    calls.length = 0;
    const second = await handle(ask({ "X-Session-Token": sessionToken, "X-Turnstile-Token": "good-1" }), ENV, deps);
    expect(second.status).toBe(403);
    expect(((await second.json()) as { error: string }).error).toBe("turnstile_failed");
    expect(calls.map((c) => c.url)).toEqual([SITEVERIFY_URL]);
  });

  it("accepts valid tokens on consecutive questions and preserves session id", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const first = await handle(ask({ "X-Turnstile-Token": "good-1" }), ENV, deps);
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
    const second = await handle(ask({ "X-Session-Token": token!, "X-Turnstile-Token": "good-2" }), ENV, deps);
    expect(second.status).toBe(200);
    expect(second.headers.get("X-Session-Token")).toBeNull();
    expect(calls[0]?.url).toBe(SITEVERIFY_URL);
    expect(calls[1]?.url).toBe("https://origin.example/api/ask");
    expect((calls[1]!.init.headers as Headers).get("X-CV-Session-Id")).toBe(sid);
  });
  it("drops a client-sent session id header", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    await handle(ask({ "X-Turnstile-Token": "good", "X-CV-Session-Id": "forged" }), ENV, deps);
    expect((calls[1]!.init.headers as Headers).get("X-CV-Session-Id")).not.toBe("forged");
  });
  it("forwards the client IP in its own header and drops a client-sent copy", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    await handle(ask({ "X-Turnstile-Token": "good", "X-CV-Client-IP": "198.51.100.66" }, undefined, "203.0.113.5"), ENV, deps);
    const headers = calls[1]!.init.headers as Headers;
    expect(headers.get("X-CV-Client-IP")).toBe("203.0.113.5");
    expect(headers.get("CF-Connecting-IP")).toBeNull();
  });
  it("forwards network class and ASN on upstream requests, overwriting client copy", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    await handle(
      ask(
        {
          "X-Turnstile-Token": "good",
          "X-CV-Net-Class": "forged-class",
          "X-CV-ASN": "999999",
        },
        undefined,
        "203.0.113.5",
        { asn: 16509, asOrganization: "Amazon Technologies Inc." },
      ),
      ENV,
      deps,
    );
    const headers = calls[1]!.init.headers as Headers;
    expect(headers.get("X-CV-Net-Class")).toBe("hosting");
    expect(headers.get("X-CV-ASN")).toBe("16509");
  });
  it("forwards other class for residential networks without client tampering", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    await handle(
      ask(
        { "X-Turnstile-Token": "good" },
        undefined,
        "198.51.100.1",
        { asn: 7922, asOrganization: "Comcast Cable Communications, LLC" },
      ),
      ENV,
      deps,
    );
    const headers = calls[1]!.init.headers as Headers;
    expect(headers.get("X-CV-Net-Class")).toBe("other");
    expect(headers.get("X-CV-ASN")).toBe("7922");
  });
  it("sends no client IP header when Cloudflare gave none", async () => {
    const { deps, calls } = makeDeps(okOrigin);
    const r = new Request(`${SITE}/api/health`, { headers: { Origin: SITE, "X-CV-Client-IP": "198.51.100.66" } });
    await handle(r, ENV, deps);
    expect((calls[0]!.init.headers as Headers).get("X-CV-Client-IP")).toBeNull();
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
      statuses.push((await handle(ask({ "X-Turnstile-Token": `good-${i}` }), ENV, deps)).status);
    }
    expect(statuses.slice(0, ASK_LIMIT.limit).every((s) => s === 200)).toBe(true);
    const last = await handle(ask({ "X-Turnstile-Token": "good-last" }), ENV, deps);
    expect(last.status).toBe(429);
    expect(last.headers.get("Retry-After")).toBe("60");
    // Another IP is unaffected.
    expect((await handle(ask({ "X-Turnstile-Token": "good-other" }, undefined, "198.51.100.7"), ENV, deps)).status).toBe(200);
  });
  it("uses the Rate Limiting binding when bound", async () => {
    const { deps } = makeDeps(okOrigin);
    const env = { ...ENV, API_LIMITER: { limit: vi.fn().mockResolvedValue({ success: false }) } };
    expect((await handle(new Request(`${SITE}/api/health`), env, deps)).status).toBe(429);
    expect(env.API_LIMITER.limit).toHaveBeenCalledWith({ key: "unknown" });
  });
  it("limits hosting questions per IP (default 2/min)", async () => {
    const { deps } = makeDeps(okOrigin);
    const cf = { asn: 16509, asOrganization: "Amazon Technologies Inc." };
    const r1 = await handle(ask({ "X-Turnstile-Token": "good-1" }, undefined, "203.0.113.10", cf), ENV, deps);
    expect(r1.status).toBe(200);
    const r2 = await handle(ask({ "X-Turnstile-Token": "good-2" }, undefined, "203.0.113.10", cf), ENV, deps);
    expect(r2.status).toBe(200);
    const r3 = await handle(ask({ "X-Turnstile-Token": "good-3" }, undefined, "203.0.113.10", cf), ENV, deps);
    expect(r3.status).toBe(429);
    expect(r3.headers.get("Retry-After")).toBe("60");
  });
  it("limits hosting questions per ASN (default 6/min) across multiple IPs", async () => {
    const { deps } = makeDeps(okOrigin);
    const cf = { asn: 16509, asOrganization: "Amazon Technologies Inc." };
    // 3 IPs x 2 requests = 6 requests (hits the 6/min ASN cap)
    for (let ip = 1; ip <= 3; ip++) {
      for (let req = 1; req <= 2; req++) {
        const r = await handle(
          ask({ "X-Turnstile-Token": `tok-${ip}-${req}` }, undefined, `203.0.113.${ip}`, cf),
          ENV,
          deps,
        );
        expect(r.status).toBe(200);
      }
    }
    // 4th IP sends 1st request -> blocked by ASN limit (6), even though its IP count is 0
    const blocked = await handle(
      ask({ "X-Turnstile-Token": "tok-4-1" }, undefined, "203.0.113.4", cf),
      ENV,
      deps,
    );
    expect(blocked.status).toBe(429);
    expect(blocked.headers.get("Retry-After")).toBe("60");
  });
  it("allows residential ISP questions beyond the hosting ASN limit", async () => {
    const { deps } = makeDeps(okOrigin);
    const cf = { asn: 7922, asOrganization: "Comcast Cable Communications, LLC" };
    // 4 IPs x 2 requests = 8 requests (would exceed hosting 6 cap, but within other 120 cap)
    for (let ip = 1; ip <= 4; ip++) {
      for (let req = 1; req <= 2; req++) {
        const r = await handle(
          ask({ "X-Turnstile-Token": `tok-comcast-${ip}-${req}` }, undefined, `198.51.100.${ip}`, cf),
          ENV,
          deps,
        );
        expect(r.status).toBe(200);
      }
    }
  });
  it("uses network Rate Limiting bindings when bound", async () => {
    const { deps } = makeDeps(okOrigin);
    const hostingCf = { asn: 16509, asOrganization: "Amazon Technologies Inc." };
    const otherCf = { asn: 7922, asOrganization: "Comcast" };

    // HOSTING_IP_LIMITER
    const envIp = {
      ...ENV,
      HOSTING_IP_LIMITER: { limit: vi.fn().mockResolvedValue({ success: false }) },
    };
    const rIp = await handle(ask({ "X-Turnstile-Token": "good" }, undefined, "203.0.113.9", hostingCf), envIp, deps);
    expect(rIp.status).toBe(429);
    expect(envIp.HOSTING_IP_LIMITER.limit).toHaveBeenCalledWith({ key: "v4:203.0.113.9" });

    // HOSTING_ASN_LIMITER
    const envAsn = {
      ...ENV,
      HOSTING_ASN_LIMITER: { limit: vi.fn().mockResolvedValue({ success: false }) },
    };
    const rAsn = await handle(ask({ "X-Turnstile-Token": "good" }, undefined, "203.0.113.9", hostingCf), envAsn, deps);
    expect(rAsn.status).toBe(429);
    expect(envAsn.HOSTING_ASN_LIMITER.limit).toHaveBeenCalledWith({ key: "asn:16509" });

    // OTHER_ASN_LIMITER
    const envOther = {
      ...ENV,
      OTHER_ASN_LIMITER: { limit: vi.fn().mockResolvedValue({ success: false }) },
    };
    const rOther = await handle(ask({ "X-Turnstile-Token": "good" }, undefined, "198.51.100.9", otherCf), envOther, deps);
    expect(rOther.status).toBe(429);
    expect(envOther.OTHER_ASN_LIMITER.limit).toHaveBeenCalledWith({ key: "asn:7922" });
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
