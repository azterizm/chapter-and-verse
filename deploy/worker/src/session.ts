/**
 * Stateless session tokens. A session starts when Turnstile passes on the first question; the
 * Worker then returns `X-Session-Token`, which the page sends on later requests until it expires.
 *
 * Format: `v1.<base64url(JSON claims)>.<base64url(HMAC-SHA256(key, "v1." + payload))>`.
 * The session id is 16 random bytes; it is forwarded to the origin as `X-CV-Session-Id` and is
 * the `sessions.session_id` key in Postgres. No IP or user data is in the token.
 */

export interface SessionClaims {
  sid: string;
  iat: number;
  exp: number;
}

const VERSION = "v1";
const MAX_TOKEN_LENGTH = 512;
const SID_RE = /^[A-Za-z0-9_-]{22}$/;
const encoder = new TextEncoder();

export function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function fromB64url(text: string): Uint8Array | null {
  if (!/^[A-Za-z0-9_-]*$/.test(text)) return null;
  const pad = text.length % 4 === 0 ? "" : "=".repeat(4 - (text.length % 4));
  try {
    const bin = atob(text.replace(/-/g, "+").replace(/_/g, "/") + pad);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
    "verify",
  ]);
}

export async function mintSession(
  secret: string,
  nowSeconds: number,
  ttlSeconds: number,
): Promise<{ token: string; claims: SessionClaims }> {
  const sid = b64url(crypto.getRandomValues(new Uint8Array(16)));
  const claims: SessionClaims = { sid, iat: nowSeconds, exp: nowSeconds + ttlSeconds };
  const payload = b64url(encoder.encode(JSON.stringify(claims)));
  const signed = `${VERSION}.${payload}`;
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", await hmacKey(secret), encoder.encode(signed)));
  return { token: `${signed}.${b64url(sig)}`, claims };
}

/** The claims of a valid, unexpired token, else null. Signature check is constant-time. */
export async function verifySession(
  token: string | null,
  secret: string,
  nowSeconds: number,
): Promise<SessionClaims | null> {
  if (!token || token.length > MAX_TOKEN_LENGTH) return null;
  const parts = token.split(".");
  if (parts.length !== 3 || parts[0] !== VERSION) return null;
  const [version, payload, sigText] = parts as [string, string, string];
  const sig = fromB64url(sigText);
  if (!sig) return null;
  const ok = await crypto.subtle.verify(
    "HMAC",
    await hmacKey(secret),
    sig,
    encoder.encode(`${version}.${payload}`),
  );
  if (!ok) return null;
  const raw = fromB64url(payload);
  if (!raw) return null;
  let claims: unknown;
  try {
    claims = JSON.parse(new TextDecoder().decode(raw));
  } catch {
    return null;
  }
  if (typeof claims !== "object" || claims === null) return null;
  const c = claims as Record<string, unknown>;
  if (typeof c.sid !== "string" || !SID_RE.test(c.sid)) return null;
  if (typeof c.iat !== "number" || typeof c.exp !== "number") return null;
  if (c.exp <= nowSeconds || c.iat > nowSeconds + 60) return null;
  return { sid: c.sid, iat: c.iat, exp: c.exp };
}
