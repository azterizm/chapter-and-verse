/** Security and CORS headers for every API response. */

export const SECURITY_HEADERS: Readonly<Record<string, string>> = {
  "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
  "Cross-Origin-Resource-Policy": "same-site",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
  "Referrer-Policy": "no-referrer",
  "Strict-Transport-Security": "max-age=31536000",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

export const SESSION_HEADER = "X-Session-Token";
export const TURNSTILE_HEADER = "X-Turnstile-Token";
/** Set by the Worker on upstream requests only; any client-sent copy is dropped. */
export const UPSTREAM_SESSION_HEADER = "X-CV-Session-Id";

const ALLOW_HEADERS = ["Content-Type", "Last-Event-ID", SESSION_HEADER, TURNSTILE_HEADER].join(", ");
const EXPOSE_HEADERS = [SESSION_HEADER, "Retry-After"].join(", ");

/** Request headers copied to the origin. Everything else (cookies, IPs, client hints) is dropped. */
export const FORWARDED_REQUEST_HEADERS = ["accept", "content-type", "last-event-id"] as const;

/** Response headers never passed back from the origin. */
const DROPPED_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "set-cookie",
  "server",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "x-powered-by",
]);

export function isAllowedOrigin(origin: string | null, allowed: ReadonlySet<string>): boolean {
  return origin !== null && allowed.has(origin);
}

export function corsHeaders(origin: string | null, allowed: ReadonlySet<string>): Record<string, string> {
  const h: Record<string, string> = { Vary: "Origin" };
  if (isAllowedOrigin(origin, allowed)) {
    h["Access-Control-Allow-Origin"] = origin as string;
    h["Access-Control-Expose-Headers"] = EXPOSE_HEADERS;
  }
  return h;
}

export function preflightHeaders(origin: string, allowed: ReadonlySet<string>): Record<string, string> {
  return {
    ...corsHeaders(origin, allowed),
    "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
    "Access-Control-Allow-Headers": ALLOW_HEADERS,
    "Access-Control-Max-Age": "600",
  };
}

/** Headers for the client response built from an origin response. */
export function responseHeaders(upstream: Headers, extra: Record<string, string>): Headers {
  const out = new Headers();
  upstream.forEach((value, name) => {
    const lower = name.toLowerCase();
    // cf-access-*: never echo the service token, even if an origin reflects it.
    if (DROPPED_RESPONSE_HEADERS.has(lower) || lower.startsWith("access-control-") || lower.startsWith("cf-access-")) {
      return;
    }
    out.append(name, value);
  });
  const contentType = out.get("content-type") ?? "";
  if (contentType.toLowerCase().startsWith("text/event-stream")) {
    // Pass SSE through without buffering or compression.
    out.set("Cache-Control", "no-cache, no-transform");
    out.set("X-Accel-Buffering", "no");
  } else if (!out.has("cache-control")) {
    out.set("Cache-Control", "no-store");
  }
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) out.set(k, v);
  for (const [k, v] of Object.entries(extra)) out.set(k, v);
  return out;
}

export function jsonError(
  status: number,
  code: string,
  message: string,
  extra: Record<string, string> = {},
): Response {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
    ...SECURITY_HEADERS,
    ...extra,
  });
  return new Response(JSON.stringify({ error: code, message }), { status, headers });
}
