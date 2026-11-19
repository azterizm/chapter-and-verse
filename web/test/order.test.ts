import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { OrderValidator, validateOrder } from "../src/api/sse";
import { parseChunks, SCENARIOS, streamBytes } from "./helpers";

interface Case {
  types: string[];
  valid: boolean;
}
const cases: Case[] = JSON.parse(readFileSync(new URL("../mock/streams/order-cases.json", import.meta.url), "utf8"));

describe("validateOrder", () => {
  it("agrees with the engine's validate_order on every recorded case", () => {
    expect(cases.length).toBeGreaterThan(300);
    expect(cases.some((c) => c.valid)).toBe(true);
    expect(cases.some((c) => !c.valid)).toBe(true);
    for (const c of cases) expect([c.types, validateOrder(c.types)]).toEqual([c.types, c.valid]);
  });

  it.each(SCENARIOS)("accepts the recorded %s stream", (name) => {
    expect(validateOrder(parseChunks([streamBytes(name)]).map((e) => e.event))).toBe(true);
  });

  it("rejects the deliberately broken stream", () => {
    expect(validateOrder(parseChunks([streamBytes("broken")]).map((e) => e.event))).toBe(false);
  });
});

describe("OrderValidator", () => {
  it("reports what was expected and keeps its state on a violation", () => {
    const v = new OrderValidator();
    expect(v.accept("route")).toEqual({ ok: true });
    const bad = v.accept("sentence");
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(bad.expected).toEqual(["notice", "clarify", "refusal", "sources", "error"]);
    expect(v.lastType).toBe("route");
    expect(v.accept("sources")).toEqual({ ok: true });
  });

  it("allows only done after error, and nothing after done", () => {
    const v = new OrderValidator();
    expect(v.accept("error").ok).toBe(true);
    expect(v.accept("notice").ok).toBe(false);
    expect(v.accept("error").ok).toBe(false);
    expect(v.accept("done").ok).toBe(true);
    expect(v.complete).toBe(true);
    expect(v.accept("notice").ok).toBe(false);
    expect(v.expected()).toEqual([]);
  });

  it("never mixes sentence and extractive", () => {
    expect(validateOrder(["route", "sources", "sentence", "extractive", "receipt", "done"])).toBe(false);
    expect(validateOrder(["route", "sources", "extractive", "sentence", "receipt", "done"])).toBe(false);
  });
});
