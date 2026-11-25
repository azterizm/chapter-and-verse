import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { pickScenario, splitEvents } from "../mock/plugin";
import { factBytesFromRaw } from "../src/api/client";
import { parseEventData, type CvEvent } from "../src/api/events";
import { validateOrder } from "../src/api/sse";
import { events, parseChunks, streamBytes } from "./helpers";

describe("mock scenario keywords", () => {
  it.each([
    ["What is the limit of the compensatory award under s.124 ERA 1996?", "answer"],
    ["uk/ukpga/1996/18/s124", "answer"],
    ["What does section 3 of the Marchwood Commercial Arbitration Act 1996 say?", "refused"],
    ["Quote section 172(1) of the Companies Act 2006", "quote"],
    ["quote s.172", "quote"],
    ["What does the National Health Service (Invalid Direction) Act 1980 say?", "text-not-held"],
    ["s.124 ERA 1996 when every provider returns 429", "extractive"],
    ["Employment Rights Act section 124", "clarify"],
    ["How long do I have to sue in tort?", "clarify-tort"],
    ["How long do I have to sue in tort? (s.2 Limitation Act 1980)", "answer"],
    ["uk/nisi/1996/1919", "out-of-coverage"],
    ["order violation please", "broken"],
    ["x".repeat(4097), "too-long"],
  ])("%s", (query, want) => {
    expect(pickScenario(query)).toBe(want);
  });

  it("provides a valid clarify-tort stream with three candidates in engine label format", () => {
    const bytes = streamBytes("clarify-tort");
    const parsed = parseChunks([bytes]);
    expect(validateOrder(parsed.map((e) => e.event))).toBe(true);
    for (const r of parsed) {
      expect(() => parseEventData(r.event, r.data)).not.toThrow();
    }
    const clarify = events("clarify-tort").find((e) => e.event.type === "clarify")?.event as
      | Extract<CvEvent, { type: "clarify" }>
      | undefined;
    expect(clarify?.candidates).toHaveLength(3);
    expect(clarify?.candidates[0]?.label).toMatch(/^s\.2 Limitation Act 1980: /);
  });

  it("splits a recorded stream into whole events", () => {
    const parts = splitEvents(Buffer.from(streamBytes("answer")));
    expect(parts).toHaveLength(8);
    expect(Buffer.concat(parts).equals(Buffer.from(streamBytes("answer")))).toBe(true);
  });

  it("provides a valid recorded-shape quote stream for s.172(1)(a) to (d)", () => {
    const bytes = streamBytes("quote");
    const parts = splitEvents(Buffer.from(bytes));
    expect(parts).toHaveLength(5);

    const parsed = parseChunks([bytes]);
    expect(validateOrder(parsed.map((e) => e.event))).toBe(true);

    const decoded = events("quote");
    const extractive = decoded.find((e) => e.event.type === "extractive")?.event as
      | Extract<CvEvent, { type: "extractive" }>
      | undefined;
    expect(extractive?.reason).toBe("quote_requested");
    expect(extractive?.windows.map((w) => w.coordinate)).toEqual([
      "uk/ukpga/2006/46/s172/1/a",
      "uk/ukpga/2006/46/s172/1/b",
      "uk/ukpga/2006/46/s172/1/c",
      "uk/ukpga/2006/46/s172/1/d",
    ]);

    for (const r of parsed) {
      expect(() => parseEventData(r.event, r.data)).not.toThrow();
    }

    const receipt = decoded.find((e) => e.event.type === "receipt");
    expect(receipt).toBeDefined();
    const factJson = factBytesFromRaw(receipt!.raw);
    expect(factJson).not.toBeNull();
    const receiptEvent = receipt!.event as Extract<CvEvent, { type: "receipt" }>;
    expect(createHash("sha256").update(factJson!, "utf8").digest("hex")).toBe(receiptEvent.answer_id);
  });
});
