/**
 * Origins the Worker forwards to, in order of preference.
 *
 * Slice 1 has one: ORIGIN_URL, the Oracle engine behind Cloudflare Tunnel. Slice 4 adds the
 * Hugging Face Space standby by appending a second entry here (for example from
 * STANDBY_ORIGIN_URL). proxy.ts already tries origins in order on connect failure, timeout or a
 * 502/503/504 before any body is sent, so failover then needs no change outside this file.
 */

import type { Env } from "./config";

export interface Origin {
  /** Short label for logs. */
  name: string;
  /** Base URL with no trailing slash, https only. */
  base: string;
}

export function parseOrigin(name: string, raw: string | undefined): Origin | null {
  if (!raw) return null;
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash) {
    return null;
  }
  return { name, base: url.origin + url.pathname.replace(/\/+$/, "") };
}

export function originsFromEnv(env: Env): Origin[] {
  const origins: Origin[] = [];
  const primary = parseOrigin("oracle", env.ORIGIN_URL);
  if (primary) origins.push(primary);
  // Slice 4: const standby = parseOrigin("hf-space", env.STANDBY_ORIGIN_URL);
  // if (standby) origins.push(standby);
  return origins;
}

/** The upstream URL for an /api/* path. Built by concatenation, never URL resolution. */
export function upstreamUrl(origin: Origin, pathname: string, search: string): string {
  if (!pathname.startsWith("/api/")) throw new Error("not an /api/ path");
  return origin.base + pathname + search;
}
