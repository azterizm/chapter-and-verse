// Server-sent events over fetch (EventSource cannot POST).
//
// SseParser follows the WHATWG event-stream rules: lines end in CRLF, LF or CR; a leading BOM
// is dropped; lines starting ":" are comments; "data" lines join with "\n"; a blank line
// dispatches; an event cut off by the end of the stream is discarded. Bytes may be split
// anywhere, including inside a CRLF pair or a UTF-8 sequence.
//
// OrderValidator is a port of contracts.validate_order (docs/ARCHITECTURE.md §5).

import { type CvEvent, type EventType, parseEventData, PayloadError } from "./events";

export interface RawEvent {
  /** The "event:" field, or "message" when absent. */
  event: string;
  /** The joined "data:" lines. */
  data: string;
}

export class SseParser {
  private readonly decoder = new TextDecoder("utf-8");
  private buf = "";
  private eventName = "";
  private dataLines: string[] = [];
  private hasData = false;

  /** Feed bytes; returns the events completed by them. */
  push(chunk: Uint8Array): RawEvent[] {
    this.buf += this.decoder.decode(chunk, { stream: true });
    return this.drain(false);
  }

  /** Feed text directly (tests and replay). */
  pushText(text: string): RawEvent[] {
    this.buf += text;
    return this.drain(false);
  }

  /** End of stream: flush the decoder. A trailing event without a blank line is dropped. */
  end(): RawEvent[] {
    this.buf += this.decoder.decode();
    const out = this.drain(true);
    if (this.buf.length > 0) {
      // A last line with no terminator: process it, but it cannot dispatch an event.
      this.line(this.buf, out);
      this.buf = "";
    }
    this.eventName = "";
    this.dataLines = [];
    this.hasData = false;
    return out;
  }

  private drain(final: boolean): RawEvent[] {
    const out: RawEvent[] = [];
    let start = 0;
    const s = this.buf;
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      if (c === 10 /* LF */) {
        this.line(s.slice(start, i), out);
        start = i + 1;
      } else if (c === 13 /* CR */) {
        if (i + 1 < s.length) {
          this.line(s.slice(start, i), out);
          if (s.charCodeAt(i + 1) === 10) i++;
          start = i + 1;
        } else if (final) {
          this.line(s.slice(start, i), out);
          start = i + 1;
        } else {
          break; // A CR at the end of the buffer may be the first half of CRLF: wait.
        }
      }
    }
    this.buf = s.slice(start);
    return out;
  }

  private line(line: string, out: RawEvent[]): void {
    if (line === "") {
      if (this.hasData) out.push({ event: this.eventName || "message", data: this.dataLines.join("\n") });
      this.eventName = "";
      this.dataLines = [];
      this.hasData = false;
      return;
    }
    if (line.charCodeAt(0) === 58 /* : */) return; // comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.charCodeAt(0) === 32) value = value.slice(1);
    if (field === "event") this.eventName = value;
    else if (field === "data") {
      this.dataLines.push(value);
      this.hasData = true;
    }
    // "id" and "retry" have no use here; unknown fields are ignored, as the spec says.
  }
}

// ---------------------------------------------------------------------------- event order

type OrderState =
  | "start"
  | "routed"
  | "clarified"
  | "refused"
  | "sourced"
  | "generating"
  | "extracted"
  | "receipted"
  | "errored"
  | "end";

const TRANSITIONS: Record<OrderState, Partial<Record<string, OrderState>>> = {
  start: { route: "routed" },
  routed: { notice: "routed", clarify: "clarified", refusal: "refused", sources: "sourced" },
  clarified: { notice: "clarified", done: "end" },
  refused: { notice: "refused", receipt: "receipted" },
  sourced: { notice: "sourced", sentence: "generating", extractive: "extracted", receipt: "receipted" },
  generating: { notice: "generating", sentence: "generating", receipt: "receipted" },
  extracted: { notice: "extracted", receipt: "receipted" },
  receipted: { notice: "receipted", done: "end" },
  errored: { done: "end" },
  end: {},
};

export type OrderCheck = { ok: true } | { ok: false; expected: string[] };

export class OrderValidator {
  private state: OrderState = "start";
  private last: string | null = null;

  /** Advance on one event type. On a violation the state does not change. */
  accept(type: string): OrderCheck {
    if (type === "error" && this.state !== "errored" && this.state !== "end") {
      this.state = "errored";
      this.last = type;
      return { ok: true };
    }
    const next = TRANSITIONS[this.state][type];
    if (next === undefined) return { ok: false, expected: this.expected() };
    this.state = next;
    this.last = type;
    return { ok: true };
  }

  /** Event types legal next, in contract order. */
  expected(): string[] {
    const keys = Object.keys(TRANSITIONS[this.state]);
    if (this.state !== "errored" && this.state !== "end") keys.push("error");
    return keys;
  }

  get complete(): boolean {
    return this.state === "end";
  }

  get lastType(): string | null {
    return this.last;
  }
}

/** True when `types` is a complete, legal event sequence (contracts.validate_order). */
export function validateOrder(types: readonly string[]): boolean {
  const v = new OrderValidator();
  for (const t of types) if (!v.accept(t).ok) return false;
  return v.complete;
}

// ---------------------------------------------------------------------------- stream reader

export type FaultCode = "order_violation" | "bad_event" | "incomplete";

export class StreamFault extends Error {
  override name = "StreamFault";
  constructor(
    readonly code: FaultCode,
    message: string,
  ) {
    super(message);
  }
}

export interface StreamItem {
  event: CvEvent;
  /** The data line exactly as received (canonical JSON). The receipt hash is over its bytes. */
  raw: string;
}

/** Check one raw event against the order and the payload contract. */
export function checkEvent(order: OrderValidator, raw: RawEvent): StreamItem {
  const check = order.accept(raw.event);
  if (!check.ok) {
    const after = order.lastType === null ? "at the start" : `after "${order.lastType}"`;
    throw new StreamFault(
      "order_violation",
      `Got "${raw.event}" ${after}. Expected ${check.expected.map((t) => `"${t}"`).join(", ")}.`,
    );
  }
  try {
    return { event: parseEventData(raw.event as EventType, raw.data), raw: raw.data };
  } catch (e) {
    if (e instanceof PayloadError) throw new StreamFault("bad_event", e.message);
    throw e;
  }
}

/**
 * Yield checked events from a response body until "done". Throws StreamFault on an order
 * violation, a malformed payload, or a stream that ends before "done"; the body is cancelled
 * first, so nothing after the fault is read.
 */
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamItem> {
  const reader = body.getReader();
  const parser = new SseParser();
  const order = new OrderValidator();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      const batch = done ? parser.end() : parser.push(value);
      for (const raw of batch) {
        if (order.complete) {
          throw new StreamFault("order_violation", `Got "${raw.event}" after "done".`);
        }
        yield checkEvent(order, raw);
      }
      if (order.complete) return;
      if (done) {
        throw new StreamFault(
          "incomplete",
          order.lastType === null
            ? "The stream ended before any event."
            : `The stream ended after "${order.lastType}", before "done".`,
        );
      }
    }
  } finally {
    // After "done", on a fault, or when the caller stops early: release the connection.
    await reader.cancel().catch(() => undefined);
  }
}
