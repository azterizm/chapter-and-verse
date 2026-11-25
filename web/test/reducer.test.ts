import { describe, expect, it } from "vitest";
import { type AppState, initialState, panelWindows, reducer, type Turn, type TurnChoice } from "../src/state/reducer";
import { candidateCitation, composeChoiceQuery, splitHighlight } from "../src/text";
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

  it("reset clears turns and activeTurnId while keeping snapshot", () => {
    let state = run("answer").state;
    state = reducer(state, { type: "start", turnId: 2, query: "again" });
    expect(state.turns).toHaveLength(2);
    expect(state.activeTurnId).toBe(2);

    const reset = reducer(state, { type: "reset" });
    expect(reset.turns).toEqual([]);
    expect(reset.activeTurnId).toBeNull();
    expect(reset.snapshot).toBe("2026-09-28");
  });
});

describe("reducer, replace, choice, and retry", () => {
  const alternatives = [
    { coordinate: "uk/ukpga/1980/58/s2", label: "s.2 Limitation Act 1980: Time limit for actions founded on tort" },
    { coordinate: "uk/ukpga/1980/58/s11", label: "s.11 Limitation Act 1980: Special time limit for actions in respect of personal injuries" },
  ];

  it("start sets question equal to query and choice to null", () => {
    const state = reducer(initialState, { type: "start", turnId: 1, query: "How long do I have to sue in tort?" });
    expect(state.turns[0]?.query).toBe("How long do I have to sue in tort?");
    expect(state.turns[0]?.question).toBe("How long do I have to sue in tort?");
    expect(state.turns[0]?.choice).toBeNull();
  });

  it("replace keeps position, removes the old turn, and sets activeTurnId", () => {
    let state = run("answer", "First question").state;
    state = reducer(state, { type: "start", turnId: 2, query: "How long do I have to sue in tort?" });
    for (const { event, raw } of events("clarify")) {
      state = reducer(state, { type: "event", turnId: 2, event, raw });
    }
    state = reducer(state, { type: "start", turnId: 3, query: "Third question" });
    expect(state.turns.map((t) => t.id)).toEqual([1, 2, 3]);
    expect(state.activeTurnId).toBe(3);

    const citation = candidateCitation(alternatives[0]!.label);
    const choice: TurnChoice = { citation, alternatives };
    state = reducer(state, {
      type: "replace",
      oldTurnId: 2,
      turnId: 4,
      query: composeChoiceQuery("How long do I have to sue in tort?", citation),
      question: "How long do I have to sue in tort?",
      choice,
    });

    expect(state.turns.map((t) => t.id)).toEqual([1, 4, 3]);
    expect(state.turns.find((t) => t.id === 2)).toBeUndefined();
    expect(state.activeTurnId).toBe(4);
    expect(state.turns[1]).toMatchObject({
      id: 4,
      phase: "streaming",
      question: "How long do I have to sue in tort?",
      query: "How long do I have to sue in tort? (s.2 Limitation Act 1980)",
      choice,
      clarify: null,
    });
  });

  it("choosing twice keeps the original question and does not nest brackets", () => {
    const question = "How long do I have to sue in tort?";
    let state = reducer(initialState, { type: "start", turnId: 1, query: question });

    const firstCitation = candidateCitation(alternatives[0]!.label);
    state = reducer(state, {
      type: "replace",
      oldTurnId: 1,
      turnId: 2,
      query: composeChoiceQuery(state.turns[0]!.question ?? state.turns[0]!.query, firstCitation),
      question: state.turns[0]!.question ?? state.turns[0]!.query,
      choice: { citation: firstCitation, alternatives },
    });

    const secondCitation = candidateCitation(alternatives[1]!.label);
    const current = state.turns[0]!;
    state = reducer(state, {
      type: "replace",
      oldTurnId: 2,
      turnId: 3,
      query: composeChoiceQuery(current.question ?? current.query, secondCitation),
      question: current.question ?? current.query,
      choice: { citation: secondCitation, alternatives },
    });

    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]?.question).toBe(question);
    expect(state.turns[0]?.query).toBe("How long do I have to sue in tort? (s.11 Limitation Act 1980)");
    expect(state.turns[0]?.choice?.citation).toBe("s.11 Limitation Act 1980");
  });

  it("retry replaces an errored turn with the same query, question, and choice", () => {
    const question = "How long do I have to sue in tort?";
    const choice: TurnChoice = { citation: "s.2 Limitation Act 1980", alternatives };
    let state = reducer(initialState, { type: "start", turnId: 1, query: question });
    state = reducer(state, {
      type: "replace",
      oldTurnId: 1,
      turnId: 2,
      query: composeChoiceQuery(question, choice.citation),
      question,
      choice,
    });
    state = reducer(state, {
      type: "failure",
      turnId: 2,
      failure: { kind: "network", message: "Connection dropped." },
    });
    expect(state.turns[0]?.phase).toBe("failed");

    const failed = state.turns[0]!;
    state = reducer(state, {
      type: "replace",
      oldTurnId: failed.id,
      turnId: 3,
      query: failed.query,
      question: failed.question ?? failed.query,
      choice: failed.choice ?? null,
    });

    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]?.id).toBe(3);
    expect(state.turns[0]?.phase).toBe("streaming");
    expect(state.turns[0]?.error).toBeNull();
    expect(state.turns[0]?.question).toBe(question);
    expect(state.turns[0]?.query).toBe("How long do I have to sue in tort? (s.2 Limitation Act 1980)");
    expect(state.turns[0]?.choice).toEqual(choice);
  });

  it("ignores events and failures for a replaced (gone) turn id", () => {
    let state = reducer(initialState, { type: "start", turnId: 1, query: "q" });
    state = reducer(state, {
      type: "replace",
      oldTurnId: 1,
      turnId: 2,
      query: "q (s.2 Limitation Act 1980)",
      question: "q",
      choice: { citation: "s.2 Limitation Act 1980", alternatives },
    });

    for (const { event, raw } of events("answer")) {
      const next = reducer(state, { type: "event", turnId: 1, event, raw });
      expect(next).toBe(state);
    }

    const afterFailure = reducer(state, {
      type: "failure",
      turnId: 1,
      failure: { kind: "network", message: "late" },
    });
    expect(afterFailure).toBe(state);
  });

  it("reset clears everything including replaced turns and choices", () => {
    let state = reducer(initialState, { type: "start", turnId: 1, query: "q" });
    state = reducer(state, {
      type: "replace",
      oldTurnId: 1,
      turnId: 2,
      query: "q (s.2 Limitation Act 1980)",
      question: "q",
      choice: { citation: "s.2 Limitation Act 1980", alternatives },
    });
    const reset = reducer(state, { type: "reset" });
    expect(reset).toEqual(initialState);
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
