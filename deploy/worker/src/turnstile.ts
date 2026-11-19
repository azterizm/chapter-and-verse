/** Turnstile server-side validation (siteverify). Fails closed on any error or timeout. */

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const MAX_TOKEN_LENGTH = 2048;

export interface TurnstileCheck {
  secret: string;
  token: string | null;
  ip: string | null;
  hostnames: readonly string[];
  action: string | null;
  timeoutMs: number;
}

export type TurnstileResult = { ok: true } | { ok: false; reason: string };

interface SiteverifyResponse {
  success?: boolean;
  hostname?: string;
  action?: string;
  "error-codes"?: string[];
}

export async function verifyTurnstile(fetchFn: typeof fetch, check: TurnstileCheck): Promise<TurnstileResult> {
  const token = check.token?.trim() ?? "";
  if (!token) return { ok: false, reason: "missing" };
  if (token.length > MAX_TOKEN_LENGTH) return { ok: false, reason: "too-long" };
  const form = new FormData();
  form.append("secret", check.secret);
  form.append("response", token);
  if (check.ip) form.append("remoteip", check.ip);
  form.append("idempotency_key", crypto.randomUUID());
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), check.timeoutMs);
  let data: SiteverifyResponse;
  try {
    const resp = await fetchFn(SITEVERIFY_URL, { method: "POST", body: form, signal: controller.signal });
    if (!resp.ok) return { ok: false, reason: `siteverify-http-${resp.status}` };
    data = (await resp.json()) as SiteverifyResponse;
  } catch {
    return { ok: false, reason: "siteverify-unreachable" };
  } finally {
    clearTimeout(timer);
  }
  if (data.success !== true) {
    return { ok: false, reason: (data["error-codes"] ?? ["rejected"]).join(",") };
  }
  if (check.hostnames.length > 0 && !check.hostnames.includes(data.hostname ?? "")) {
    return { ok: false, reason: "hostname-mismatch" };
  }
  if (check.action !== null && data.action !== check.action) {
    return { ok: false, reason: "action-mismatch" };
  }
  return { ok: true };
}
