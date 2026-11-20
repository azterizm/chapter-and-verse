import { readFileSync } from "node:fs";
import { parseEventData, type CvEvent } from "../src/api/events";
import { SseParser, type RawEvent } from "../src/api/sse";

export const SCENARIOS = ["answer", "refused", "text-not-held", "extractive", "clarify", "out-of-coverage", "too-long"] as const;

export function streamBytes(name: string): Uint8Array {
  return new Uint8Array(readFileSync(new URL(`../mock/streams/${name}.sse`, import.meta.url)));
}

/** Parse bytes fed in the given chunks. */
export function parseChunks(chunks: Uint8Array[]): RawEvent[] {
  const p = new SseParser();
  const out: RawEvent[] = [];
  for (const c of chunks) out.push(...p.push(c));
  out.push(...p.end());
  return out;
}

export function splitAt(bytes: Uint8Array, cuts: number[]): Uint8Array[] {
  const out: Uint8Array[] = [];
  let at = 0;
  for (const c of [...cuts].sort((a, b) => a - b)) {
    out.push(bytes.subarray(at, c));
    at = c;
  }
  out.push(bytes.subarray(at));
  return out;
}

export function events(name: string): { event: CvEvent; raw: string }[] {
  return parseChunks([streamBytes(name)]).map((r) => ({ event: parseEventData(r.event, r.data), raw: r.data }));
}

export function bodyFrom(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(ctl) {
      if (i < chunks.length) ctl.enqueue(chunks[i++]!);
      else ctl.close();
    },
  });
}

/** A small deterministic PRNG for repeatable random cuts. */
export function rng(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

export const enc = (s: string) => new TextEncoder().encode(s);
