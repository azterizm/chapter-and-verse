import { describe, expect, it } from "vitest";
import { pickScenario, splitEvents } from "../mock/plugin";
import { streamBytes } from "./helpers";

describe("mock scenario keywords", () => {
  it.each([
    ["What is the limit of the compensatory award under s.124 ERA 1996?", "answer"],
    ["uk/ukpga/1996/18/s124", "answer"],
    ["What does section 3 of the Marchwood Commercial Arbitration Act 1996 say?", "refused"],
    ["What does the National Health Service (Invalid Direction) Act 1980 say?", "text-not-held"],
    ["s.124 ERA 1996 when every provider returns 429", "extractive"],
    ["Employment Rights Act section 124", "clarify"],
    ["uk/nisi/1996/1919", "out-of-coverage"],
    ["order violation please", "broken"],
    ["x".repeat(4097), "too-long"],
  ])("%s", (query, want) => {
    expect(pickScenario(query)).toBe(want);
  });

  it("splits a recorded stream into whole events", () => {
    const parts = splitEvents(Buffer.from(streamBytes("answer")));
    expect(parts).toHaveLength(8);
    expect(Buffer.concat(parts).equals(Buffer.from(streamBytes("answer")))).toBe(true);
  });
});
