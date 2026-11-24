import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { factBytesFromRaw, sha256Hex } from "../src/api/client";
import type { CvEvent } from "../src/api/events";
import { parseEventData, PayloadError } from "../src/api/events";
import { readEvents, StreamFault } from "../src/api/sse";
import { bodyFrom, enc, events, rng, SCENARIOS, splitAt, streamBytes } from "./helpers";

async function collect(body: ReadableStream<Uint8Array>): Promise<{ types: string[]; fault: StreamFault | null }> {
  const types: string[] = [];
  try {
    for await (const item of readEvents(body)) types.push(item.event.type);
    return { types, fault: null };
  } catch (e) {
    if (e instanceof StreamFault) return { types, fault: e };
    throw e;
  }
}

describe("readEvents", () => {
  it.each(SCENARIOS)("reads the %s stream at random chunk boundaries", async (name) => {
    const bytes = streamBytes(name);
    const want = events(name).map((e) => e.event.type);
    const next = rng(name.length);
    for (let n = 0; n < 25; n++) {
      const cuts = Array.from({ length: 6 }, () => Math.floor(next() * bytes.length));
      const got = await collect(bodyFrom(splitAt(bytes, cuts)));
      expect(got).toEqual({ types: want, fault: null });
    }
  });

  it("stops at an order violation and yields nothing after it", async () => {
    const got = await collect(bodyFrom([streamBytes("broken")]));
    expect(got.types).toEqual(["route"]);
    expect(got.fault?.code).toBe("order_violation");
    expect(got.fault?.message).toBe(
      'Got "sentence" after "route". Expected "notice", "clarify", "refusal", "sources", "error".',
    );
  });

  it("reports a stream that ends before done", async () => {
    const bytes = streamBytes("answer");
    const cut = new TextDecoder().decode(bytes).indexOf("event: receipt");
    const got = await collect(bodyFrom([bytes.subarray(0, cut)]));
    expect(got.fault?.code).toBe("incomplete");
    expect(got.fault?.message).toBe('The stream ended after "sentence", before "done".');
  });

  it("reports an empty stream", async () => {
    const got = await collect(bodyFrom([]));
    expect(got.fault?.code).toBe("incomplete");
  });

  it("reports a payload that breaks the contract", async () => {
    const s = 'event: route\ndata: {"type":"route","status":"ROUTE_BOUNDED"}\n\n';
    const got = await collect(bodyFrom([enc(s)]));
    expect(got.fault?.code).toBe("bad_event");
  });

  it("reports an event after done", async () => {
    const s = new TextDecoder().decode(streamBytes("clarify")) + 'event: notice\ndata: {"code":"x","message":"y","type":"notice"}\n\n';
    const got = await collect(bodyFrom([enc(s)]));
    expect(got.fault?.code).toBe("order_violation");
  });
});

describe("parseEventData", () => {
  it("rejects unknown types, a type mismatch and bad JSON", () => {
    expect(() => parseEventData("message", "{}")).toThrow(PayloadError);
    expect(() => parseEventData("done", '{"type":"route"}')).toThrow(PayloadError);
    expect(() => parseEventData("done", "{")).toThrow(PayloadError);
    expect(() => parseEventData("done", '{"type":"done","timings":{"first_source_ms":null,"first_sentence_ms":null}}')).toThrow(
      PayloadError,
    );
  });

  it("rejects a window with an unknown temporal status", () => {
    const [, sources] = events("answer");
    const raw = sources!.raw.replace('"IN_FORCE"', '"MAYBE"');
    expect(() => parseEventData("sources", raw)).toThrow(PayloadError);
  });

  it("accepts quote_requested extractive reason and rejects unknown reasons", () => {
    const ev = events("extractive").find((e) => e.event.type === "extractive")!;
    const quoteRaw = ev.raw.replace('"providers_unavailable"', '"quote_requested"');
    const parsed = parseEventData("extractive", quoteRaw);
    expect(parsed.type).toBe("extractive");
    if (parsed.type === "extractive") expect(parsed.reason).toBe("quote_requested");

    const badRaw = ev.raw.replace('"providers_unavailable"', '"unknown_reason"');
    expect(() => parseEventData("extractive", badRaw)).toThrow(PayloadError);
  });
});

describe("receipt bytes", () => {
  it("extracts the fact JSON byte for byte, so its SHA-256 is the answer id", async () => {
    for (const name of ["answer", "refused", "text-not-held", "extractive", "out-of-coverage"]) {
      const r = events(name).find((e) => e.event.type === "receipt")!;
      const ev = r.event as Extract<CvEvent, { type: "receipt" }>;
      const json = factBytesFromRaw(r.raw);
      expect(json).not.toBeNull();
      expect(createHash("sha256").update(json!, "utf8").digest("hex")).toBe(ev.answer_id);
      expect(await sha256Hex(json!)).toBe(ev.answer_id);
      expect(JSON.parse(json!)).toEqual(ev.fact);
    }
  });

  it("keeps Python's float form, which JSON.stringify would lose", () => {
    const r = events("answer").find((e) => e.event.type === "receipt")!;
    expect(factBytesFromRaw(r.raw)).toContain('"latency_ms":1412.0');
  });

  it("returns null for a line not in canonical form", () => {
    expect(factBytesFromRaw('{"fact":{},"answer_id":"x","type":"receipt"}')).toBeNull();
    expect(factBytesFromRaw('{"answer_id":"x","fact":{,"type":"receipt"}')).toBeNull();
  });
});
