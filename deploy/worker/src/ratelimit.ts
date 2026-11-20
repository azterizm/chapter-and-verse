/**
 * Coarse per-IP limits.
 *
 * Primary: the Workers Rate Limiting binding ([[ratelimits]] in wrangler.toml), counted per
 * Cloudflare location and eventually consistent. Fallback, used when a binding is absent or its
 * call throws: an in-isolate token bucket with the same limit and period. The fallback only sees
 * the requests that reach this isolate, so it is weaker; it exists so the Worker never runs
 * without a limit.
 *
 * Limits (keep in step with wrangler.toml):
 *   API_LIMITER  every /api/* request         60 per 60 s per IP
 *   ASK_LIMITER  POST requests (questions)    10 per 60 s per IP
 */

import type { RateLimiter } from "./config";

export interface Limit {
  limit: number;
  periodSeconds: number;
}

export const API_LIMIT: Limit = { limit: 60, periodSeconds: 60 };
export const ASK_LIMIT: Limit = { limit: 10, periodSeconds: 60 };
export const HOSTING_ASN_LIMIT: Limit = { limit: 6, periodSeconds: 60 };
export const HOSTING_IP_LIMIT: Limit = { limit: 2, periodSeconds: 60 };
export const OTHER_ASN_LIMIT: Limit = { limit: 120, periodSeconds: 60 };

/**
 * The rate-limit key for an Autonomous System Number (ASN).
 * Unknown, non-integer, or non-positive values map to "unknown".
 */
export function asnKey(asn: number | null | undefined): string {
  if (typeof asn !== "number" || !Number.isInteger(asn) || asn <= 0) return "unknown";
  return `asn:${asn}`;
}

/**
 * The rate-limit key for a client IP. IPv6 clients are keyed by their /64, since one subscriber
 * usually holds a whole /64. Unknown or malformed input maps to a shared "unknown" key.
 */
export function clientKey(ip: string | null): string {
  if (!ip) return "unknown";
  const value = ip.trim();
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(value)) return `v4:${value}`;
  const groups = expandIPv6(value);
  if (!groups) return "unknown";
  return `v6:${groups.slice(0, 4).join(":")}::/64`;
}

/** Eight lowercase hex groups without leading zeros, or null when not an IPv6 address. */
export function expandIPv6(value: string): string[] | null {
  let text = value.toLowerCase();
  const zone = text.indexOf("%");
  if (zone >= 0) text = text.slice(0, zone);
  if (!/^[0-9a-f:.]+$/.test(text) || !text.includes(":")) return null;
  // An embedded IPv4 tail (::ffff:1.2.3.4) becomes two hex groups.
  const v4 = /(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(text);
  if (v4) {
    const b = v4.slice(1).map(Number);
    if (b.some((n) => n > 255)) return null;
    const hi = ((b[0]! << 8) | b[1]!).toString(16);
    const lo = ((b[2]! << 8) | b[3]!).toString(16);
    text = `${text.slice(0, v4.index)}${hi}:${lo}`;
  }
  const halves = text.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const missing = 8 - head.length - tail.length;
  if (halves.length === 1 ? missing !== 0 : missing < 1) return null;
  const groups = [...head, ...Array<string>(halves.length === 2 ? missing : 0).fill("0"), ...tail];
  if (groups.length !== 8 || groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
  return groups.map((g) => parseInt(g, 16).toString(16));
}

/** In-isolate token bucket: capacity `limit`, refilled at `limit / periodSeconds` per second. */
export class TokenBucket {
  private readonly buckets = new Map<string, { tokens: number; last: number }>();

  constructor(
    private readonly spec: Limit,
    private readonly maxKeys = 10000,
  ) {}

  take(key: string, nowMs: number): boolean {
    const rate = this.spec.limit / (this.spec.periodSeconds * 1000);
    let b = this.buckets.get(key);
    if (!b) {
      if (this.buckets.size >= this.maxKeys) this.evict(nowMs);
      b = { tokens: this.spec.limit, last: nowMs };
      this.buckets.set(key, b);
    }
    b.tokens = Math.min(this.spec.limit, b.tokens + Math.max(0, nowMs - b.last) * rate);
    b.last = nowMs;
    if (b.tokens < 1) return false;
    b.tokens -= 1;
    return true;
  }

  get size(): number {
    return this.buckets.size;
  }

  /** Drop full buckets (idle keys); if none are full, drop the oldest half. */
  private evict(nowMs: number): void {
    const full = this.spec.periodSeconds * 1000;
    for (const [k, b] of this.buckets) if (nowMs - b.last >= full) this.buckets.delete(k);
    if (this.buckets.size < this.maxKeys) return;
    let n = Math.ceil(this.buckets.size / 2);
    for (const k of this.buckets.keys()) {
      if (n-- <= 0) break;
      this.buckets.delete(k);
    }
  }
}

/** True when the request may proceed. Uses the binding when present, else the bucket. */
export async function allow(
  binding: RateLimiter | undefined,
  bucket: TokenBucket,
  key: string,
  nowMs: number,
): Promise<boolean> {
  if (binding) {
    try {
      const { success } = await binding.limit({ key });
      return success;
    } catch {
      // Fall through to the in-isolate bucket.
    }
  }
  return bucket.take(key, nowMs);
}
