import { describe, expect, it } from "vitest";
import { type AppState, initialState, panelWindows, reducer, type Turn } from "../src/state/reducer";
import { splitHighlight } from "../src/text";
import { events } from "./helpers";

function run(name: string, query = "q"): { state: AppState; turn: Turn } {
  let state = reducer(initialState, { type: "start", turnId: 1, query });
  for (const { event, raw } of events(name)) state = reducer(state, { type: "event", turnId: 1, event, raw });
  return { state, turn: state.turns[0]! };
}

describe("reducer, per scenario", () => {
  it("answer: three cited sentences, sources, a notice, a receipt and timings", () => {
    const { state, turn } = run("answer");
    expect(turn.phase).toBe("done");
    expect(state.snapshot).toBe("2026-09-28");
    expect(turn.route?.status).toBe("ROUTE_BOUNDED");
    expect(turn.windows.map((w) => w.passage_id)).toEqual(["P1", "P2", "P3"]);
    expect(turn.sentences).toHaveLength(3);
    expect(turn.sentences.map((s) => s.citations.map((c) => c.label))).toEqual([
      ["s.124(1) Employment Rights Act 1996"],
      ["s.124(1ZA) Employment Rights Act 1996"],
      ["s.124(1A) Employment Rights Act 1996"],
    ]);
    expect(turn.notices.map((n) => n.code)).toEqual(["provider_fallback"]);
    expect(turn.receipt?.exact).toBe(true);
    expect(turn.receipt?.fact.mode).toBe("generated");
    expect(turn.timings?.first_sentence_ms).toBe(1693);
    expect(turn.clarify ?? turn.refusal ?? turn.extractive ?? turn.error).toBeNull();
  });

  it("refused: the invented Act gets a refusal card with a suggestion and no sources", () => {
    const { turn } = run("refused");
    expect(turn.refusal?.kind).toBe("EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND");
    expect(turn.refusal?.suggestions.map((s) => s.label)).toEqual(["Arbitration Act 1996"]);
    expect(turn.refusal?.link).toBeNull();
    expect(turn.windows).toEqual([]);
    expect(turn.sentences).toEqual([]);
    expect(turn.receipt?.fact.mode).toBe("refusal");
    expect(turn.phase).toBe("done");
  });

  it("text-not-held: a refusal with the legislation.gov.uk link", () => {
    const { turn } = run("text-not-held");
    expect(turn.refusal?.kind).toBe("TEXT_NOT_HELD");
    expect(turn.refusal?.link).toBe("https://www.legislation.gov.uk/ukpga/1980/15");
    expect(turn.route?.coordinates).toEqual(["uk/ukpga/1980/15"]);
  });

  it("extractive: banner reason, highlighted windows, no sentences", () => {
    const { turn } = run("extractive");
    expect(turn.extractive?.reason).toBe("providers_unavailable");
    expect(turn.sentences).toEqual([]);
    const ws = panelWindows(turn);
    expect(ws.map((w) => w.passage_id)).toEqual(["P1", "P2"]);
    const [, marked] = splitHighlight(ws[1]!.text, ws[1]!.highlight);
    expect(marked).toBe("the lower of— (a) £123,543, and (b) 52 multiplied by a week’s pay of the person concerned.");
    expect(turn.receipt?.fact.mode).toBe("extractive");
    expect(turn.timings?.first_sentence_ms).toBeNull();
  });

  it("clarify: candidates, no receipt", () => {
    const { turn } = run("clarify");
    expect(turn.clarify?.candidates.map((c) => c.coordinate)).toEqual(["uk/ukpga/1996/18/s124", "uk/nisi/1996/1919"]);
    expect(turn.receipt).toBeNull();
    expect(turn.phase).toBe("done");
  });

  it("too-long: a server error before routing", () => {
    const { turn } = run("too-long");
    expect(turn.error).toEqual({ source: "server", code: "query_too_long", message: "The question is longer than 4096 bytes." });
    expect(turn.route).toBeNull();
    expect(turn.phase).toBe("done");
  });
});

describe("reducer, failures and focus", () => {
  it("a stream fault marks the turn failed and keeps what had arrived", () => {
    let state = reducer(initialState, { type: "start", turnId: 1, query: "q" });
    const [route] = events("answer");
    state = reducer(state, { type: "event", turnId: 1, ...route! });
    state = reducer(state, {
      type: "failure",
      turnId: 1,
      failure: { kind: "fault", code: "order_violation", message: 'Got "sentence" after "route".' },
    });
    const turn = state.turns[0]!;
    expect(turn.phase).toBe("failed");
    expect(turn.route).not.toBeNull();
    expect(turn.error?.code).toBe("order_violation");
    expect(turn.error?.message).toBe('The answer stream broke the protocol, so it was stopped. Got "sentence" after "route".');
  });

  it("an abort marks the turn stopped with no error", () => {
    let state = reducer(initialState, { type: "start", turnId: 1, query: "q" });
    state = reducer(state, { type: "failure", turnId: 1, failure: { kind: "aborted" } });
    expect(state.turns[0]!.phase).toBe("stopped");
    expect(state.turns[0]!.error).toBeNull();
  });

  it("a failure after done changes nothing", () => {
    const { state } = run("answer");
    const after = reducer(state, { type: "failure", turnId: 1, failure: { kind: "network", message: "x" } });
    expect(after).toEqual(state);
  });

  it("route snapshot wins over health; health fills an empty snapshot", () => {
    let state = reducer(initialState, { type: "health", snapshot: "2026-01-01" });
    expect(state.snapshot).toBe("2026-01-01");
    state = run("answer").state;
    expect(reducer(state, { type: "health", snapshot: "2026-01-01" }).snapshot).toBe("2026-09-28");
  });

  it("a new turn becomes active; focus moves the source panel back", () => {
    let state = run("answer").state;
    state = reducer(state, { type: "start", turnId: 2, query: "again" });
    expect(state.activeTurnId).toBe(2);
    state = reducer(state, { type: "focus", turnId: 1 });
    expect(state.activeTurnId).toBe(1);
  });
});

describe("splitHighlight", () => {
  it("counts code points, as Python does", () => {
    const text = "a\u{1F600}bcd";
    expect(splitHighlight(text, [2, 4])).toEqual(["a\u{1F600}", "bc", "d"]);
    expect(splitHighlight("abc", null)).toEqual(["abc", "", ""]);
    expect(splitHighlight("abc", [2, 99])).toEqual(["ab", "c", ""]);
  });
});
