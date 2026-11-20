/**
 * Worker configuration, read from plain vars (wrangler.toml [vars]) and secrets
 * (`wrangler secret put`). Secrets, all required: TURNSTILE_SECRET, SESSION_SIGNING_KEY, and
 * ACCESS_CLIENT_ID / ACCESS_CLIENT_SECRET, the Cloudflare Access service token that the origin
 * hostname demands. Without the Access pair the Worker forwards nothing (fail closed).
 */

/** The subset of the Workers Rate Limiting binding this Worker uses. */
export interface RateLimiter {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  ORIGIN_URL: string;
  ALLOWED_ORIGINS?: string;
  ORIGIN_TIMEOUT_MS?: string;
  MAX_BODY_BYTES?: string;
  SESSION_TTL_SECONDS?: string;
  TURNSTILE_HOSTNAMES?: string;
  TURNSTILE_ACTION?: string;
  // Network rate limit vars (wrangler.toml [vars])
  HOSTING_ASN_PER_MINUTE?: string;
  HOSTING_IP_PER_MINUTE?: string;
  OTHER_ASN_PER_MINUTE?: string;
  // Secrets
  TURNSTILE_SECRET?: string;
  SESSION_SIGNING_KEY?: string;
  ACCESS_CLIENT_ID?: string;
  ACCESS_CLIENT_SECRET?: string;
  // Rate Limiting bindings ([[ratelimits]] in wrangler.toml)
  ASK_LIMITER?: RateLimiter;
  API_LIMITER?: RateLimiter;
  HOSTING_ASN_LIMITER?: RateLimiter;
  HOSTING_IP_LIMITER?: RateLimiter;
  OTHER_ASN_LIMITER?: RateLimiter;
}

export interface Config {
  allowedOrigins: ReadonlySet<string>;
  originTimeoutMs: number;
  maxBodyBytes: number;
  sessionTtlSeconds: number;
  turnstileHostnames: readonly string[];
  turnstileAction: string | null;
  turnstileSecret: string | null;
  sessionKey: string | null;
  /** CF-Access-Client-Id and -Secret for every origin request; null when either is unset. */
  accessHeaders: Readonly<Record<string, string>> | null;
  hostingAsnPerMinute: number;
  hostingIpPerMinute: number;
  otherAsnPerMinute: number;
}

export const DEFAULTS = {
  originTimeoutMs: 4000,
  // JSON envelope plus escaping around a query of up to MAX_QUERY_BYTES (4096), which the
  // engine enforces on the query itself.
  maxBodyBytes: 12288,
  sessionTtlSeconds: 7200,
  hostingAsnPerMinute: 6,
  hostingIpPerMinute: 2,
  otherAsnPerMinute: 120,
} as const;

/** Minimum length of SESSION_SIGNING_KEY; shorter keys are treated as unset (fail closed). */
export const MIN_SESSION_KEY_LENGTH = 32;

export function list(value: string | undefined): string[] {
  return (value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

export function intVar(value: string | undefined, fallback: number, min: number, max: number): number {
  if (value === undefined || value.trim() === "") return fallback;
  const n = Number(value);
  if (!Number.isInteger(n) || n < min || n > max) return fallback;
  return n;
}

export function readConfig(env: Env): Config {
  const id = env.ACCESS_CLIENT_ID?.trim();
  const secret = env.ACCESS_CLIENT_SECRET?.trim();
  const accessHeaders = id && secret ? { "CF-Access-Client-Id": id, "CF-Access-Client-Secret": secret } : null;
  const key = env.SESSION_SIGNING_KEY ?? "";
  return {
    allowedOrigins: new Set(list(env.ALLOWED_ORIGINS)),
    originTimeoutMs: intVar(env.ORIGIN_TIMEOUT_MS, DEFAULTS.originTimeoutMs, 500, 30000),
    maxBodyBytes: intVar(env.MAX_BODY_BYTES, DEFAULTS.maxBodyBytes, 256, 65536),
    sessionTtlSeconds: intVar(env.SESSION_TTL_SECONDS, DEFAULTS.sessionTtlSeconds, 300, 86400),
    turnstileHostnames: list(env.TURNSTILE_HOSTNAMES),
    turnstileAction: env.TURNSTILE_ACTION?.trim() || null,
    turnstileSecret: env.TURNSTILE_SECRET?.trim() || null,
    sessionKey: key.length >= MIN_SESSION_KEY_LENGTH ? key : null,
    accessHeaders,
    hostingAsnPerMinute: intVar(env.HOSTING_ASN_PER_MINUTE, DEFAULTS.hostingAsnPerMinute, 1, 10000),
    hostingIpPerMinute: intVar(env.HOSTING_IP_PER_MINUTE, DEFAULTS.hostingIpPerMinute, 1, 10000),
    otherAsnPerMinute: intVar(env.OTHER_ASN_PER_MINUTE, DEFAULTS.otherAsnPerMinute, 1, 10000),
  };
}
