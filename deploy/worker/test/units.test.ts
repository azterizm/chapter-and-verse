import { describe, expect, it, vi } from "vitest";

import { readCapped, isJsonContentType } from "../src/body";
import { intVar, list, readConfig } from "../src/config";
import { parseOrigin, upstreamUrl } from "../src/origins";
import { forward, pingAll } from "../src/proxy";
import { TokenBucket, allow, clientKey, expandIPv6 } from "../src/ratelimit";
import { b64url, fromB64url, mintSession, verifySession } from "../src/session";
import { SITEVERIFY_URL, verifyTurnstile } from "../src/turnstile";

const KEY = "k".repeat(40);

describe("clientKey", () => {
  it("keys IPv4 by address", () => {
    expect(clientKey("203.0.113.9")).toBe("v4:203.0.113.9");
  });
  it("keys IPv6 by /64 in canonical form", () => {
    expect(clientKey("2001:db8:0:1:aaaa::1")).toBe("v6:2001:db8:0:1::/64");
    expect(clientKey("2001:0DB8:0000:0001:ffff:ffff:ffff:ffff")).toBe("v6:2001:db8:0:1::/64");
    expect(clientKey("::1")).toBe("v6:0:0:0:0::/64");
  });
  it("handles embedded IPv4 and zones", () => {
    expect(expandIPv6("::ffff:192.0.2.1")).toEqual(["0", "0", "0", "0", "0", "ffff", "c000", "201"]);
    expect(expandIPv6("fe80::1%eth0")?.[0]).toBe("fe80");
  });
  it("maps junk to unknown", () => {
    for (const bad of [null, "", "not-an-ip", "1:2:3", "1::2::3", "12345::", "::ffff:300.1.1.1"]) {
      expect(clientKey(bad)).toBe("unknown");
    }
  });
});

describe("TokenBucket", () => {
  it("allows the limit, then refills at limit/period", () => {
    const b = new TokenBucket({ limit: 3, periodSeconds: 60 });
    expect([b.take("a", 0), b.take("a", 0), b.take("a", 0), b.take("a", 0)]).toEqual([true, true, true, false]);
    expect(b.take("b", 0)).toBe(true);
    expect(b.take("a", 19_999)).toBe(false);
    expect(b.take("a", 20_000)).toBe(true);
  });
  it("bounds memory", () => {
    const b = new TokenBucket({ limit: 1, periodSeconds: 10 }, 100);
    for (let i = 0; i < 1000; i++) b.take(`k${i}`, i);
    expect(b.size).toBeLessThanOrEqual(100);
  });
  it("prefers the binding and falls back when it throws", async () => {
    const bucket = new TokenBucket({ limit: 1, periodSeconds: 60 });
    const deny = { limit: vi.fn().mockResolvedValue({ success: false }) };
    expect(await allow(deny, bucket, "x", 0)).toBe(false);
    expect(deny.limit).toHaveBeenCalledWith({ key: "x" });
    const broken = { limit: vi.fn().mockRejectedValue(new Error("down")) };
    expect(await allow(broken, bucket, "x", 0)).toBe(true);
    expect(await allow(broken, bucket, "x", 0)).toBe(false);
    expect(await allow(undefined, new TokenBucket({ limit: 1, periodSeconds: 60 }), "y", 0)).toBe(true);
  });
});

describe("session tokens", () => {
  it("round-trips", async () => {
    const { token, claims } = await mintSession(KEY, 1000, 60);
    expect(claims.sid).toMatch(/^[A-Za-z0-9_-]{22}$/);
    expect(await verifySession(token, KEY, 1030)).toEqual(claims);
  });
  it("rejects expiry, wrong key, tampering and junk", async () => {
    const { token } = await mintSession(KEY, 1000, 60);
    expect(await verifySession(token, KEY, 1060)).toBeNull();
    expect(await verifySession(token, "x".repeat(40), 1001)).toBeNull();
    const [v, payload, sig] = token.split(".") as [string, string, string];
    const forged = b64url(new TextEncoder().encode(JSON.stringify({ sid: "A".repeat(22), iat: 1000, exp: 99999 })));
    expect(await verifySession(`${v}.${forged}.${sig}`, KEY, 1001)).toBeNull();
    expect(await verifySession(`${v}.${payload}.${sig.slice(0, -2)}AA`, KEY, 1001)).toBeNull();
    for (const junk of [null, "", "v1", "v2.a.b", "v1.a.b.c", "v1.!!.??", "x".repeat(600)]) {
      expect(await verifySession(junk, KEY, 1001)).toBeNull();
    }
  });
  it("base64url round-trips", () => {
    const bytes = new Uint8Array([0, 250, 251, 252, 253, 254, 255]);
    expect(fromB64url(b64url(bytes))).toEqual(bytes);
    expect(fromB64url("a+b/")).toBeNull();
  });
});

describe("body cap", () => {
  const post = (body: string, headers: Record<string, string> = {}) =>
    new Request("https://x/api/ask", { method: "POST", body, headers });
  it("accepts up to the cap", async () => {
    const r = await readCapped(post("a".repeat(4096)), 4096);
    expect(r.ok && r.bytes.byteLength).toBe(4096);
  });
  it("refuses over the cap by Content-Length or by stream", async () => {
    expect((await readCapped(post("a".repeat(4097)), 4096)).ok).toBe(false);
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new Uint8Array(3000));
        c.enqueue(new Uint8Array(3000));
        c.close();
      },
    });
    const req = new Request("https://x/api/ask", { method: "POST", body: stream, duplex: "half" } as RequestInit);
    expect((await readCapped(req, 4096)).ok).toBe(false);
  });
  it("checks JSON content type", () => {
    expect(isJsonContentType("application/json; charset=utf-8")).toBe(true);
    expect(isJsonContentType("text/plain")).toBe(false);
    expect(isJsonContentType(null)).toBe(false);
  });
});

describe("config and origins", () => {
  it("parses lists and bounded integers", () => {
    expect(list(" a, ,b ")).toEqual(["a", "b"]);
    expect(intVar("abc", 7, 1, 10)).toBe(7);
    expect(intVar("50", 7, 1, 10)).toBe(7);
    expect(intVar("5", 7, 1, 10)).toBe(5);
  });
  it("treats a short signing key as unset", () => {
    expect(readConfig({ ORIGIN_URL: "", SESSION_SIGNING_KEY: "short" }).sessionKey).toBeNull();
    expect(readConfig({ ORIGIN_URL: "", SESSION_SIGNING_KEY: KEY }).sessionKey).toBe(KEY);
  });
  it("accepts https origins only", () => {
    expect(parseOrigin("o", "https://o.example/")).toEqual({ name: "o", base: "https://o.example" });
    expect(parseOrigin("o", "http://o.example")).toBeNull();
    expect(parseOrigin("o", "https://u:p@o.example")).toBeNull();
    expect(parseOrigin("o", "not a url")).toBeNull();
    expect(parseOrigin("o", undefined)).toBeNull();
  });
  it("builds upstream URLs by concatenation", () => {
    const o = { name: "o", base: "https://o.example" };
    expect(upstreamUrl(o, "/api/ask", "?x=1")).toBe("https://o.example/api/ask?x=1");
    expect(() => upstreamUrl(o, "//evil.example/api/", "")).toThrow();
  });
});

describe("forward", () => {
  const req = { method: "GET", pathname: "/api/health", search: "", headers: new Headers(), body: null };
  const a = { name: "a", base: "https://a.example" };
  const b = { name: "b", base: "https://b.example" };
  it("fails over on 503 and on network error (Slice 4 path)", async () => {
    const f = vi.fn(async (url: string | URL | Request) =>
      String(url).startsWith("https://a.") ? new Response("down", { status: 503 }) : new Response("ok"),
    );
    const r = await forward(f as typeof fetch, [a, b], req, 1000);
    expect(r.ok && r.origin.name).toBe("b");
    const g = vi.fn(async (url: string | URL | Request) => {
      if (String(url).startsWith("https://a.")) throw new TypeError("connect failed");
      return new Response("ok");
    });
    expect((await forward(g as typeof fetch, [a, b], req, 1000)).ok).toBe(true);
  });
  it("returns the last origin's 503 rather than failing", async () => {
    const f = vi.fn(async () => new Response("down", { status: 503 }));
    const r = await forward(f as typeof fetch, [a], req, 1000);
    expect(r.ok && r.response.status).toBe(503);
  });
  it("times out while waiting for headers", async () => {
    const hang = (_u: unknown, init?: RequestInit) =>
      new Promise<Response>((_, reject) => {
        init?.signal?.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
      });
    const r = await forward(hang as typeof fetch, [a], req, 50);
    expect(r).toEqual({ ok: false, reason: "timeout" });
  });
  it("pings every origin without throwing", async () => {
    const f = vi.fn(async (url: string | URL | Request) => {
      if (String(url).startsWith("https://b.")) throw new TypeError("down");
      return new Response("{}", { status: 200 });
    });
    const res = await pingAll(f as typeof fetch, [a, b], { "CF-Access-Client-Id": "id" }, 1000);
    expect(res.map((r) => [r.origin, r.status])).toEqual([
      ["a", 200],
      ["b", null],
    ]);
    expect(f.mock.calls[0]?.[0]).toBe("https://a.example/api/health");
  });
});

describe("turnstile", () => {
  const base = { secret: "s", token: "t", ip: "203.0.113.9", hostnames: ["site.example"], action: "ask", timeoutMs: 1000 };
  const reply = (body: object, status = 200) => vi.fn(async () => new Response(JSON.stringify(body), { status }));
  it("passes a good token and posts the form", async () => {
    const f = reply({ success: true, hostname: "site.example", action: "ask" });
    expect(await verifyTurnstile(f as unknown as typeof fetch, base)).toEqual({ ok: true });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(SITEVERIFY_URL);
    const form = init.body as FormData;
    expect(form.get("secret")).toBe("s");
    expect(form.get("response")).toBe("t");
    expect(form.get("remoteip")).toBe("203.0.113.9");
  });
  it("fails closed", async () => {
    const cases: Array<[typeof fetch, Partial<typeof base>]> = [
      [reply({ success: false, "error-codes": ["invalid-input-response"] }) as unknown as typeof fetch, {}],
      [reply({ success: true, hostname: "evil.example", action: "ask" }) as unknown as typeof fetch, {}],
      [reply({ success: true, hostname: "site.example", action: "other" }) as unknown as typeof fetch, {}],
      [reply({}, 500) as unknown as typeof fetch, {}],
      [vi.fn().mockRejectedValue(new TypeError("down")) as unknown as typeof fetch, {}],
      [reply({ success: true }) as unknown as typeof fetch, { token: null as unknown as string }],
      [reply({ success: true }) as unknown as typeof fetch, { token: "x".repeat(3000) }],
    ];
    for (const [f, override] of cases) {
      expect((await verifyTurnstile(f, { ...base, ...override })).ok).toBe(false);
    }
  });
});
