/**
 * Chapter & Verse API edge: Cloudflare Worker on /api/*.
 *
 * Order of checks for each request:
 *   1. path under /api/, method allowed, CORS origin allowed (OPTIONS answered here);
 *   2. per-IP rate limit (all requests; POST also has the stricter question limit);
 *   3. POST only: JSON content type, body at most MAX_BODY_BYTES (12288; the engine caps the
 *      query itself at MAX_QUERY_BYTES, 4096);
 *   4. POST only: a valid X-Session-Token, or, on POST /api/ask, a Turnstile token that passes
 *      siteverify, after which a new session token is returned in X-Session-Token;
 *   5. forward to the origin with the Access service token and a short header timeout,
 *      streaming the response (SSE) back. No token configured: 503, nothing is forwarded.
 */

import { isJsonContentType, readCapped } from "./body";
import { readConfig, type Config, type Env } from "./config";
import {
  FORWARDED_REQUEST_HEADERS,
  SESSION_HEADER,
  TURNSTILE_HEADER,
  UPSTREAM_CLIENT_IP_HEADER,
  UPSTREAM_SESSION_HEADER,
  corsHeaders,
  isAllowedOrigin,
  jsonError,
  preflightHeaders,
  responseHeaders,
  SECURITY_HEADERS,
} from "./headers";
import { originsFromEnv } from "./origins";
import { forward, pingAll } from "./proxy";
import { API_LIMIT, ASK_LIMIT, TokenBucket, allow, clientKey } from "./ratelimit";
import { mintSession, verifySession } from "./session";
import { verifyTurnstile } from "./turnstile";

export type { Env } from "./config";

export interface Deps {
  fetch: typeof fetch;
  now: () => number;
  apiBucket: TokenBucket;
  askBucket: TokenBucket;
}

const SITEVERIFY_TIMEOUT_MS = 5000;
const HEALTH_TIMEOUT_MS = 10000;
const ASK_PATH = "/api/ask";

// Per-isolate fallback buckets, shared by requests served by this isolate.
const isolateDeps: Omit<Deps, "fetch"> = {
  now: () => Date.now(),
  apiBucket: new TokenBucket(API_LIMIT),
  askBucket: new TokenBucket(ASK_LIMIT),
};

export async function handle(request: Request, env: Env, deps: Deps): Promise<Response> {
  const url = new URL(request.url);
  const config = readConfig(env);
  const origin = request.headers.get("Origin");
  const cors = corsHeaders(origin, config.allowedOrigins);

  if (!url.pathname.startsWith("/api/")) return jsonError(404, "not_found", "Not found.", cors);

  // A browser request from a page we do not serve. Requests without Origin (curl, the cron)
  // pass this check and still face the limits and the session rule.
  if (origin !== null && !isAllowedOrigin(origin, config.allowedOrigins)) {
    return jsonError(403, "origin_not_allowed", "Origin not allowed.", cors);
  }

  const method = request.method.toUpperCase();
  if (method === "OPTIONS") {
    if (origin === null) return jsonError(400, "bad_preflight", "Missing Origin.", cors);
    return new Response(null, {
      status: 204,
      headers: { ...SECURITY_HEADERS, ...preflightHeaders(origin, config.allowedOrigins) },
    });
  }
  if (method !== "GET" && method !== "HEAD" && method !== "POST") {
    return jsonError(405, "method_not_allowed", "Method not allowed.", { ...cors, Allow: "GET, HEAD, POST, OPTIONS" });
  }

  if (config.accessHeaders === null) {
    console.error(JSON.stringify({ event: "misconfigured", missing: "access service token" }));
    return jsonError(503, "misconfigured", "The service is not configured.", cors);
  }

  // 2. Rate limits.
  const ip = request.headers.get("CF-Connecting-IP");
  const key = clientKey(ip);
  const nowMs = deps.now();
  const limited = { ...cors, "Retry-After": String(API_LIMIT.periodSeconds) };
  if (!(await allow(env.API_LIMITER, deps.apiBucket, key, nowMs))) {
    return jsonError(429, "rate_limited", "Too many requests. Try again in a minute.", limited);
  }
  if (method === "POST" && !(await allow(env.ASK_LIMITER, deps.askBucket, key, nowMs))) {
    return jsonError(429, "rate_limited", "Too many questions. Try again in a minute.", limited);
  }

  // 3 and 4. Body cap and session, POST only.
  let body: Uint8Array | null = null;
  let sessionId: string | null = null;
  let newSessionToken: string | null = null;
  if (method === "POST") {
    if (!isJsonContentType(request.headers.get("Content-Type"))) {
      return jsonError(415, "unsupported_media_type", "Send application/json.", cors);
    }
    const capped = await readCapped(request, config.maxBodyBytes);
    if (!capped.ok) {
      return jsonError(413, "body_too_large", `Request body is over ${config.maxBodyBytes} bytes.`, cors);
    }
    body = capped.bytes;

    const session = await resolveSession(request, url, config, deps, ip);
    if ("error" in session) return session.error(cors);
    sessionId = session.sid;
    newSessionToken = session.newToken;
  }

  // 5. Forward.
  const upstreamHeaders = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) upstreamHeaders.set(name, value);
  }
  for (const [k, v] of Object.entries(config.accessHeaders)) upstreamHeaders.set(k, v);
  if (sessionId !== null) upstreamHeaders.set(UPSTREAM_SESSION_HEADER, sessionId);
  if (ip !== null && ip.trim() !== "") upstreamHeaders.set(UPSTREAM_CLIENT_IP_HEADER, ip.trim());

  const result = await forward(
    deps.fetch,
    originsFromEnv(env),
    { method, pathname: url.pathname, search: url.search, headers: upstreamHeaders, body },
    config.originTimeoutMs,
  );
  if (!result.ok) {
    if (result.reason === "no-origin") {
      return jsonError(503, "misconfigured", "The service is not configured.", cors);
    }
    const status = result.reason === "timeout" ? 504 : 502;
    return jsonError(status, "origin_unavailable", "The service is unavailable. Try again shortly.", {
      ...cors,
      "Retry-After": "30",
    });
  }
  const extra: Record<string, string> = { ...cors };
  if (newSessionToken !== null) extra[SESSION_HEADER] = newSessionToken;
  return new Response(result.response.body, {
    status: result.response.status,
    statusText: result.response.statusText,
    headers: responseHeaders(result.response.headers, extra),
  });
}

type SessionOutcome =
  | { sid: string; newToken: string | null }
  | { error: (cors: Record<string, string>) => Response };

async function resolveSession(
  request: Request,
  url: URL,
  config: Config,
  deps: Deps,
  ip: string | null,
): Promise<SessionOutcome> {
  if (config.sessionKey === null || config.turnstileSecret === null) {
    // Fail closed: without both secrets no question is accepted.
    return { error: (c) => jsonError(503, "misconfigured", "The service is not configured.", c) };
  }
  const nowS = Math.floor(deps.now() / 1000);
  const existing = await verifySession(request.headers.get(SESSION_HEADER), config.sessionKey, nowS);
  if (existing) return { sid: existing.sid, newToken: null };

  if (url.pathname !== ASK_PATH) {
    return { error: (c) => jsonError(401, "session_required", "Start by asking a question.", c) };
  }
  const token = request.headers.get(TURNSTILE_HEADER);
  if (!token) {
    return { error: (c) => jsonError(401, "turnstile_required", "Complete the check, then ask again.", c) };
  }
  const check = await verifyTurnstile(deps.fetch, {
    secret: config.turnstileSecret,
    token,
    ip,
    hostnames: config.turnstileHostnames,
    action: config.turnstileAction,
    timeoutMs: SITEVERIFY_TIMEOUT_MS,
  });
  if (!check.ok) {
    console.log(JSON.stringify({ event: "turnstile_failed", reason: check.reason }));
    return { error: (c) => jsonError(403, "turnstile_failed", "The check did not pass. Reload and try again.", c) };
  }
  const minted = await mintSession(config.sessionKey, nowS, config.sessionTtlSeconds);
  return { sid: minted.claims.sid, newToken: minted.token };
}

export async function healthCron(env: Env, fetchFn: typeof fetch): Promise<void> {
  const config = readConfig(env);
  if (config.accessHeaders === null) {
    console.error(JSON.stringify({ event: "health", error: "access service token not configured" }));
    return;
  }
  const results = await pingAll(fetchFn, originsFromEnv(env), config.accessHeaders, HEALTH_TIMEOUT_MS);
  for (const r of results) {
    const line = JSON.stringify({ event: "health", ...r });
    if (r.status === 200) console.log(line);
    else console.error(line);
  }
}

export default {
  async fetch(request, env): Promise<Response> {
    return handle(request, env, { ...isolateDeps, fetch: (input, init) => fetch(input, init) });
  },
  async scheduled(_controller, env, ctx): Promise<void> {
    ctx.waitUntil(healthCron(env, (input, init) => fetch(input, init)));
  },
} satisfies ExportedHandler<Env>;
