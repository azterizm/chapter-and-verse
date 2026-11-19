/** Request body reading with a hard byte cap (MAX_BODY_BYTES, 12288 by default). */

export type CappedBody = { ok: true; bytes: Uint8Array } | { ok: false };

/**
 * Read at most `max` bytes. Returns `{ ok: false }` when Content-Length or the actual stream
 * exceeds the cap; the stream is cancelled as soon as the cap is passed.
 */
export async function readCapped(request: Request, max: number): Promise<CappedBody> {
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    const n = Number(declared);
    if (!Number.isFinite(n) || n < 0 || n > max) return { ok: false };
  }
  if (request.body === null) return { ok: true, bytes: new Uint8Array(0) };
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      await reader.cancel();
      return { ok: false };
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const c of chunks) {
    bytes.set(c, offset);
    offset += c.byteLength;
  }
  return { ok: true, bytes };
}

export function isJsonContentType(value: string | null): boolean {
  if (value === null) return false;
  return value.split(";")[0]?.trim().toLowerCase() === "application/json";
}
