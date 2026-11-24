// Mock API for `npm run dev` and `npm run preview`. Replays the recorded streams in
// mock/streams/ (written by mock/record.py from the engine's contract) with realistic delays.
//
// Each event is written in two or three pieces cut at arbitrary byte offsets, including inside
// a UTF-8 sequence such as "£", and ": keep-alive" comment lines are sent between some events,
// so the browser parser sees the same awkward input the real network can produce.

import { readFileSync } from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Connect, Plugin } from "vite";

const STREAMS = join(dirname(fileURLToPath(import.meta.url)), "streams");
const MAX_QUERY_BYTES = 4096;

export type Scenario =
  | "answer"
  | "refused"
  | "text-not-held"
  | "extractive"
  | "quote"
  | "clarify"
  | "out-of-coverage"
  | "too-long"
  | "broken";

/** Pick a recorded stream by keyword. Anything unmatched gets the s.124 answer. */
export function pickScenario(query: string): Scenario {
  const q = query.toLowerCase();
  if (Buffer.byteLength(query, "utf8") > MAX_QUERY_BYTES) return "too-long";
  if (q.includes("marchwood")) return "refused";
  if (q.includes("quote")) return "quote";
  if (q.includes("429") || q.includes("extractive")) return "extractive";
  if (q.includes("invalid direction") || q.includes("1980/15") || q.includes("title only")) return "text-not-held";
  if (q.includes("violation")) return "broken";
  if (q.startsWith("uk/") && !q.startsWith("uk/ukpga/")) return "out-of-coverage";
  if (q.includes("124") && !q.includes("1996")) return "clarify";
  return "answer";
}

/** Split a recorded stream into its events (each ends in a blank line). */
export function splitEvents(stream: Buffer): Buffer[] {
  const out: Buffer[] = [];
  let start = 0;
  for (;;) {
    const end = stream.indexOf("\n\n", start);
    if (end === -1) break;
    out.push(stream.subarray(start, end + 2));
    start = end + 2;
  }
  return out;
}

const DELAY_MS: Record<string, [number, number]> = {
  route: [60, 120],
  notice: [150, 250],
  clarify: [80, 120],
  refusal: [80, 140],
  sources: [180, 280],
  sentence: [650, 1100],
  extractive: [400, 600],
  receipt: [150, 250],
  error: [20, 40],
  done: [30, 60],
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function eventType(chunk: Buffer): string {
  const m = /^event: (\w+)/.exec(chunk.toString("utf8"));
  return m?.[1] ?? "message";
}

/** Cut points that land inside the first multi-byte UTF-8 sequence when there is one. */
function cuts(chunk: Buffer, seed: number): number[] {
  const multibyte = chunk.findIndex((b) => b >= 0xc0);
  const a = multibyte > 0 ? multibyte + 1 : Math.max(1, Math.floor(chunk.length * 0.37));
  const b = Math.max(a + 1, Math.min(chunk.length - 1, a + 7 + (seed % 23)));
  return b < chunk.length ? [a, b] : [a];
}

async function readBody(req: IncomingMessage): Promise<string> {
  const parts: Buffer[] = [];
  for await (const p of req) parts.push(p as Buffer);
  return Buffer.concat(parts).toString("utf8");
}

async function replay(res: ServerResponse, scenario: Scenario, alive: () => boolean): Promise<void> {
  const events = splitEvents(readFileSync(join(STREAMS, `${scenario}.sse`)));
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
  });
  let i = 0;
  for (const ev of events) {
    const [lo, hi] = DELAY_MS[eventType(ev)] ?? [100, 200];
    await sleep(lo + ((i * 7919) % (hi - lo + 1)));
    if (!alive()) return;
    if (i % 3 === 2) res.write(": keep-alive\n\n");
    let at = 0;
    for (const cut of [...cuts(ev, i), ev.length]) {
      res.write(ev.subarray(at, cut));
      at = cut;
      await sleep(5);
    }
    i++;
  }
  res.end();
}

function middleware(): Connect.NextHandleFunction {
  return (req, res, next) => {
    const url = (req.url ?? "").split("?")[0];
    if (url === "/api/health" && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", corpus_id: "mock", snapshot: "2026-09-28" }));
      return;
    }
    if (url !== "/api/ask") return next();
    if (req.method !== "POST") {
      res.writeHead(405).end();
      return;
    }
    let closed = false;
    res.on("close", () => {
      closed = true;
    });
    readBody(req)
      .then((body) => {
        const parsed: unknown = JSON.parse(body);
        const query =
          typeof parsed === "object" && parsed !== null && typeof (parsed as { query?: unknown }).query === "string"
            ? (parsed as { query: string }).query
            : "";
        return replay(res, pickScenario(query), () => !closed);
      })
      .catch(() => {
        if (!res.headersSent) res.writeHead(400, { "Content-Type": "text/plain" });
        res.end("bad request");
      });
  };
}

export function mockApi(): Plugin {
  return {
    name: "chapter-and-verse-mock-api",
    configureServer(server) {
      server.middlewares.use(middleware());
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware());
    },
  };
}
