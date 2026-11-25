import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
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

describe("engine index streams", () => {
  const dirs: string[] = [];

  afterEach(() => {
    for (const d of dirs.splice(0)) {
      rmSync(d, { recursive: true, force: true });
    }
  });

  function makeEngineStreams(entries: Record<string, { question: string; keywords?: string[] }>): string {
    const dir = mkdtempSync(join(tmpdir(), "cv-mock-"));
    dirs.push(dir);
    const engineDir = join(dir, "engine");
    mkdirSync(engineDir, { recursive: true });
    for (const name of Object.keys(entries)) {
      writeFileSync(join(engineDir, `${name}.sse`), `event: done\ndata: {"timings":{"first_source_ms":null,"first_sentence_ms":null,"total_ms":1},"type":"done"}\n\n`);
    }
    writeFileSync(join(engineDir, "index.json"), JSON.stringify(entries));
    return dir;
  }

  it("picks recorded engine stream by exact trimmed case-insensitive question or keyword", () => {
    const dir = makeEngineStreams({
      tort: {
        question: "What is the limitation period for bringing a claim in tort under the Limitation Act 1980?",
        keywords: ["limitation period", "limitation act 1980"],
      },
      "tort-clarify": {
        question: "How long do I have to sue in tort?",
        keywords: ["sue in tort"],
      },
      "tort-chosen": {
        question: "How long do I have to sue in tort? (s.2 Limitation Act 1980)",
        keywords: ["s.2 limitation act 1980"],
      },
      "quote-172": {
        question: "Quote the exact statutory wording of section 172(1)(a) to (d) of the Companies Act 2006.",
        keywords: ["172(1)", "companies act 2006"],
      },
      "reject-30": {
        question: "How long is the short-term right to reject under the Consumer Rights Act 2015?",
        keywords: ["short-term right to reject", "consumer rights act 2015"],
      },
      "era-124": {
        question: "What is the limit of the compensatory award under the Employment Rights Act 1996?",
        keywords: ["compensatory award"],
      },
      "not-in-act": {
        question: "What is the national minimum wage hourly rate under the Employment Rights Act 1996?",
        keywords: ["national minimum wage"],
      },
      "out-of-coverage": {
        question: "Code civil article 1240",
        keywords: ["code civil"],
      },
    });

    // Exact match (trimmed, case-insensitive)
    expect(pickScenario("  HOW LONG DO I HAVE TO SUE IN TORT?  ", dir)).toBe("engine/tort-clarify");
    expect(pickScenario("how long do i have to sue in tort? (s.2 limitation act 1980)", dir)).toBe("engine/tort-chosen");
    expect(pickScenario("Code civil article 1240", dir)).toBe("engine/out-of-coverage");

    // Keyword match
    expect(pickScenario("Quote section 172(1) of the Companies Act 2006", dir)).toBe("engine/quote-172");
    expect(pickScenario("How long is the short-term right to reject?", dir)).toBe("engine/reject-30");
    expect(pickScenario("How long do I have to sue in tort? (s.11 Limitation Act 1980)", dir)).toBe("engine/tort-chosen");
    expect(pickScenario("What is the national minimum wage?", dir)).toBe("engine/not-in-act");
    expect(pickScenario("limitation period in tort", dir)).toBe("engine/tort");

    // Synthetic error paths remain unchanged
    expect(pickScenario("What does section 3 of the Marchwood Commercial Arbitration Act 1996 say?", dir)).toBe("refused");
    expect(pickScenario("s.124 ERA 1996 when every provider returns 429", dir)).toBe("extractive");
    expect(pickScenario("What does the National Health Service (Invalid Direction) Act 1980 say?", dir)).toBe("text-not-held");
    expect(pickScenario("order violation please", dir)).toBe("broken");
    expect(pickScenario("uk/nisi/1996/1919", dir)).toBe("out-of-coverage");
    expect(pickScenario("Employment Rights Act section 124", dir)).toBe("clarify");
    expect(pickScenario("x".repeat(4097), dir)).toBe("too-long");

    // Unmatched query gets era-124 when present
    expect(pickScenario("Something completely unmatched", dir)).toBe("engine/era-124");
  });

  it("falls back to synthetic answer when era-124 is absent from engine index", () => {
    const dir = makeEngineStreams({
      tort: {
        question: "What is the limitation period for bringing a claim in tort under the Limitation Act 1980?",
        keywords: ["limitation period"],
      },
    });
    expect(pickScenario("Something completely unmatched", dir)).toBe("answer");
    expect(pickScenario("What is the limitation period?", dir)).toBe("engine/tort");
  });
});
