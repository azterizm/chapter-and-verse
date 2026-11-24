// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatAnswerCopyText } from "@/components/ActionRow";
import { Turn } from "@/components/Turn";
import { ToastProvider } from "@/components/ui/toast";
import type { AnswerFactObject, Window } from "@/api/events";
import type { Turn as TurnData } from "@/state/reducer";
import {
  ACTION_COPY,
  ACTION_DETAILS,
  ACTION_NEW_QUESTION,
  ACTION_RETRY,
  ACTION_VERIFY,
  COPIED_TOAST,
  COPY_FAILED_TOAST,
  EXTRACTIVE_REASON,
  RECEIPT_MISMATCH,
  RECEIPT_VERIFIED,
  SOURCE_LINK_LABEL,
  STATUS_STEPS,
} from "@/text";

function makeWindow(overrides: Partial<Window> = {}): Window {
  return {
    passage_id: "P1",
    coordinate: "uk/ukpga/1980/58/s2",
    heading: "Limitation Act 1980, section 2",
    text: "An action founded on tort shall not be brought after the expiration of six years from the date on which the cause of action accrued.",
    status: "IN_FORCE",
    version_date: "1991-02-01",
    source_url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
    highlight: null,
    ...overrides,
  };
}

function makeFact(overrides: Partial<AnswerFactObject> = {}): AnswerFactObject {
  return {
    query_sha256: "0".repeat(64),
    route_status: "ROUTE_BOUNDED",
    coordinates: ["uk/ukpga/1980/58/s2"],
    passages: { P1: "uk/ukpga/1980/58/s2" },
    version_dates: { "uk/ukpga/1980/58": "1991-02-01" },
    spans: {},
    sentences: [],
    corpus_id: "corpus-1",
    calls: [],
    policy_hash: "policy-1",
    mode: "generated",
    schema: 1,
    ...overrides,
  };
}

function makeTurn(overrides: Partial<TurnData> = {}): TurnData {
  return {
    id: 1,
    query: "How long do I have to sue in tort?",
    phase: "done",
    route: {
      type: "route",
      status: "ROUTE_BOUNDED",
      next_action: "RETRIEVE_BOUNDED",
      coordinates: ["uk/ukpga/1980/58/s2"],
      corrections: [],
      messages: [],
      index_snapshot: "2026-09-28",
    },
    clarify: null,
    refusal: null,
    windows: [makeWindow()],
    sentences: [
      {
        type: "sentence",
        sentence: {
          index: 0,
          text: "An action founded on tort must be brought within six years.",
          verdict: "supported",
          passage_ids: ["P1"],
          reasons: ["GROUNDED"],
          rollbacks: 0,
        },
        citations: [
          {
            passage_id: "P1",
            coordinate: "uk/ukpga/1980/58/s2",
            label: "s.2 Limitation Act 1980",
            url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
          },
        ],
      },
    ],
    extractive: null,
    notices: [],
    receipt: {
      answer_id: "abc123def456abc123def456abc123def456abc123def456abc123def456abcd",
      fact: makeFact(),
      json: '{"schema":1}',
      exact: true,
    },
    error: null,
    timings: {
      first_source_ms: 120,
      first_sentence_ms: 480,
      total_ms: 950,
    },
    ...overrides,
  };
}

function renderTurn(
  turn: TurnData,
  handlers: {
    busy?: boolean;
    onAsk?: (q: string) => void;
    onShowSource?: (turnId: number, passageId: string) => void;
    onNewQuestion?: () => void;
  } = {},
) {
  const onAsk = handlers.onAsk ?? vi.fn();
  const onShowSource = handlers.onShowSource ?? vi.fn();
  const result = render(
    <ToastProvider>
      <Turn
        turn={turn}
        busy={handlers.busy ?? false}
        onAsk={onAsk}
        onShowSource={onShowSource}
        onNewQuestion={handlers.onNewQuestion}
      />
    </ToastProvider>,
  );
  return { ...result, onAsk, onShowSource };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Turn answer rendering", () => {
  it("renders short provision chip labels for one instrument, keeps full label in aria-label/title, and triggers onShowSource", async () => {
    const user = userEvent.setup();
    const turn = makeTurn({
      sentences: [
        {
          type: "sentence",
          sentence: {
            index: 0,
            text: "The time limit in tort is six years.",
            verdict: "supported",
            passage_ids: ["P1", "P2"],
            reasons: ["GROUNDED"],
            rollbacks: 0,
          },
          citations: [
            {
              passage_id: "P1",
              coordinate: "uk/ukpga/1980/58/s2",
              label: "s.2 Limitation Act 1980",
              url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
            },
            {
              passage_id: "P2",
              coordinate: "uk/ukpga/1980/58/s11",
              label: "s.11 Limitation Act 1980",
              url: "https://www.legislation.gov.uk/ukpga/1980/58/section/11",
            },
          ],
        },
      ],
    });

    const { onShowSource } = renderTurn(turn);

    const chip1 = screen.getByRole("button", { name: "s.2 Limitation Act 1980" });
    const chip2 = screen.getByRole("button", { name: "s.11 Limitation Act 1980" });
    expect(chip1).toHaveTextContent("s.2");
    expect(chip1).toHaveAttribute("title", "s.2 Limitation Act 1980");
    expect(chip2).toHaveTextContent("s.11");
    expect(chip2).toHaveAttribute("title", "s.11 Limitation Act 1980");

    await user.click(chip1);
    expect(onShowSource).toHaveBeenCalledWith(1, "P1");
  });

  it("adds the year to citation chips when two or more instruments appear in the same answer", () => {
    const turn = makeTurn({
      sentences: [
        {
          type: "sentence",
          sentence: {
            index: 0,
            text: "Claims differ between limitation and employment statutes.",
            verdict: "supported",
            passage_ids: ["P1", "P2"],
            reasons: ["GROUNDED"],
            rollbacks: 0,
          },
          citations: [
            {
              passage_id: "P1",
              coordinate: "uk/ukpga/1980/58/s2",
              label: "s.2 Limitation Act 1980",
              url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
            },
            {
              passage_id: "P2",
              coordinate: "uk/ukpga/1996/18/s124/1",
              label: "s.124(1) Employment Rights Act 1996",
              url: "https://www.legislation.gov.uk/ukpga/1996/18/section/124/1",
            },
          ],
        },
      ],
    });

    renderTurn(turn);

    const chip1 = screen.getByRole("button", { name: "s.2 Limitation Act 1980" });
    const chip2 = screen.getByRole("button", { name: "s.124(1) Employment Rights Act 1996" });
    expect(chip1).toHaveTextContent("s.2 (1980)");
    expect(chip1).toHaveAttribute("title", "s.2 Limitation Act 1980");
    expect(chip2).toHaveTextContent("s.124(1) (1996)");
    expect(chip2).toHaveAttribute("title", "s.124(1) Employment Rights Act 1996");
  });

  it("renders abstention sentences in muted style with no chips", () => {
    const turn = makeTurn({
      sentences: [
        {
          type: "sentence",
          sentence: {
            index: 0,
            text: "No further rule can be stated from these provisions.",
            verdict: "refused",
            passage_ids: [],
            reasons: ["NOT_SUPPORTED"],
            rollbacks: 0,
          },
          citations: [
            {
              passage_id: "P1",
              coordinate: "uk/ukpga/1980/58/s2",
              label: "s.2 Limitation Act 1980",
              url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
            },
          ],
        },
      ],
    });

    renderTurn(turn);

    const abstention = screen.getByText("No further rule can be stated from these provisions.");
    expect(abstention).toHaveClass("text-muted-foreground");
    expect(screen.queryByRole("button", { name: "s.2 Limitation Act 1980" })).not.toBeInTheDocument();
  });

  it("renders StatusLine while streaming before sentences arrive, then removes it", () => {
    const step0 = makeTurn({
      phase: "streaming",
      route: null,
      windows: [],
      sentences: [],
      receipt: null,
      timings: null,
    });
    const { rerender } = renderTurn(step0);
    expect(screen.getByText(STATUS_STEPS[0])).toHaveAttribute("role", "status");

    const step1 = makeTurn({
      phase: "streaming",
      windows: [],
      sentences: [],
      receipt: null,
      timings: null,
    });
    rerender(
      <ToastProvider>
        <Turn turn={step1} busy={true} onAsk={vi.fn()} onShowSource={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getByText(STATUS_STEPS[1])).toHaveAttribute("role", "status");

    const step2 = makeTurn({
      phase: "streaming",
      sentences: [],
      receipt: null,
      timings: null,
    });
    rerender(
      <ToastProvider>
        <Turn turn={step2} busy={true} onAsk={vi.fn()} onShowSource={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getByText(STATUS_STEPS[2])).toHaveAttribute("role", "status");

    const streamingWithSentence = makeTurn({
      phase: "streaming",
      receipt: null,
      timings: null,
    });
    rerender(
      <ToastProvider>
        <Turn turn={streamingWithSentence} busy={true} onAsk={vi.fn()} onShowSource={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.queryByText(STATUS_STEPS[2])).not.toBeInTheDocument();
  });

  it("keeps non-fallback notices inline and hides provider_fallback and meta lines behind Details", async () => {
    const user = userEvent.setup();
    const turn = makeTurn({
      notices: [
        {
          type: "notice",
          code: "provider_fallback",
          message: "Groq was at its limit. Cloudflare Workers AI answered instead.",
        },
        {
          type: "notice",
          code: "corrections_applied",
          message: "Limtation -> Limitation",
        },
      ],
      timings: {
        first_source_ms: 212.4,
        first_sentence_ms: 1693.0,
        total_ms: 3920.7,
      },
    });
    renderTurn(turn);

    const list = screen.getByRole("list", { name: "Notices" });
    expect(list).toHaveTextContent("Spelling corrected. Limtation -> Limitation");
    expect(list).not.toHaveTextContent("Backup model used");
    expect(screen.queryByText(/Answer id/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/First sentence/i)).not.toBeInTheDocument();

    const detailsBtn = screen.getByRole("button", { name: ACTION_DETAILS });
    expect(detailsBtn).toHaveAttribute("aria-expanded", "false");

    await user.click(detailsBtn);
    expect(detailsBtn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Answer id/i)).toBeInTheDocument();
    expect(screen.getByText("First sentence 1.69 s, total 3.92 s")).toBeInTheDocument();
    expect(screen.getByText("Backup model used")).toBeInTheDocument();

    await user.click(detailsBtn);
    expect(detailsBtn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Backup model used")).not.toBeInTheDocument();
  });
});

describe("QuoteBlock and extractive answers", () => {
  it("renders full text, link, and per-block copy buttons for quote_requested", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    const w1 = makeWindow({
      passage_id: "P1",
      heading: "Companies Act 2006, section 172(1)",
      text: "A director of a company must act in the way he considers, in good faith, would be most likely to promote the success of the company.",
      source_url: "https://www.legislation.gov.uk/ukpga/2006/46/section/172/1",
    });

    const turn = makeTurn({
      sentences: [],
      windows: [w1],
      extractive: {
        type: "extractive",
        reason: "quote_requested",
        windows: [w1],
      },
    });

    renderTurn(turn);

    expect(screen.getByText(w1.heading)).toBeInTheDocument();
    expect(screen.getByText(w1.text)).toBeInTheDocument();
    const extLink = screen.getByRole("link", { name: new RegExp(SOURCE_LINK_LABEL) });
    expect(extLink).toHaveAttribute("href", w1.source_url);

    const copyBtn = screen.getByRole("button", { name: `Copy ${w1.heading}` });
    await user.click(copyBtn);

    expect(writeText).toHaveBeenCalledWith(`${w1.heading}\n${w1.text}\n${w1.source_url}`);
    expect(screen.getByRole("status")).toHaveTextContent(COPIED_TOAST);
  });

  it("renders notice and QuoteBlock without per-block copy for other extractive reasons", () => {
    const w1 = makeWindow();
    const turn = makeTurn({
      sentences: [],
      windows: [w1],
      extractive: {
        type: "extractive",
        reason: "providers_unavailable",
        windows: [w1],
      },
    });

    renderTurn(turn);

    expect(screen.getByText(EXTRACTIVE_REASON.providers_unavailable)).toBeInTheDocument();
    expect(screen.getByText(w1.text)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: `Copy ${w1.heading}` })).not.toBeInTheDocument();
  });
});

describe("ActionRow", () => {
  it("appears only when turn phase is done", () => {
    const streaming = makeTurn({ phase: "streaming", receipt: null, timings: null });
    const { rerender } = renderTurn(streaming);

    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_VERIFY })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_NEW_QUESTION })).not.toBeInTheDocument();

    const failed = makeTurn({
      phase: "failed",
      receipt: null,
      timings: null,
      error: { source: "client", code: "network", message: "The connection failed." },
    });
    rerender(
      <ToastProvider>
        <Turn turn={failed} busy={false} onAsk={vi.fn()} onShowSource={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();

    const done = makeTurn({ phase: "done" });
    rerender(
      <ToastProvider>
        <Turn turn={done} busy={false} onAsk={vi.fn()} onShowSource={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getByRole("button", { name: ACTION_COPY })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: ACTION_VERIFY })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: ACTION_NEW_QUESTION })).toBeInTheDocument();
  });

  it("copies exact R4 text format for a fixture turn and handles failure", async () => {
    const user = userEvent.setup();
    const turn = makeTurn({
      sentences: [
        {
          type: "sentence",
          sentence: {
            index: 0,
            text: "First sentence.",
            verdict: "supported",
            passage_ids: ["P1"],
            reasons: ["GROUNDED"],
            rollbacks: 0,
          },
          citations: [
            {
              passage_id: "P1",
              coordinate: "uk/ukpga/1980/58/s2",
              label: "s.2 Limitation Act 1980",
              url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
            },
          ],
        },
        {
          type: "sentence",
          sentence: {
            index: 1,
            text: "Second sentence.",
            verdict: "supported",
            passage_ids: ["P1", "P2"],
            reasons: ["GROUNDED"],
            rollbacks: 0,
          },
          citations: [
            {
              passage_id: "P1",
              coordinate: "uk/ukpga/1980/58/s2",
              label: "s.2 Limitation Act 1980",
              url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
            },
            {
              passage_id: "P2",
              coordinate: "uk/ukpga/1980/58/s11",
              label: "s.11 Limitation Act 1980",
              url: "https://www.legislation.gov.uk/ukpga/1980/58/section/11",
            },
          ],
        },
        {
          type: "sentence",
          sentence: {
            index: 2,
            text: "No more can be verified.",
            verdict: "refused",
            passage_ids: [],
            reasons: ["NOT_SUPPORTED"],
            rollbacks: 0,
          },
          citations: [],
        },
      ],
    });

    const expected = [
      "First sentence. [s.2 Limitation Act 1980]\nSecond sentence. [s.2 Limitation Act 1980, s.11 Limitation Act 1980]\nNo more can be verified.",
      "Sources:\n- s.2 Limitation Act 1980: https://www.legislation.gov.uk/ukpga/1980/58/section/2\n- s.11 Limitation Act 1980: https://www.legislation.gov.uk/ukpga/1980/58/section/11",
      `Answer id: ${turn.receipt?.answer_id}`,
    ].join("\n\n");

    expect(formatAnswerCopyText(turn)).toBe(expected);

    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    renderTurn(turn);

    await user.click(screen.getByRole("button", { name: ACTION_COPY }));
    expect(writeText).toHaveBeenCalledWith(expected);
    expect(screen.getByRole("status")).toHaveTextContent(COPIED_TOAST);

    // Failure path: both Clipboard API and execCommand fail
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
      configurable: true,
    });
    Object.defineProperty(document, "execCommand", {
      value: vi.fn(() => false),
      configurable: true,
    });

    await user.click(screen.getByRole("button", { name: ACTION_COPY }));
    expect(screen.getByRole("status")).toHaveTextContent(COPY_FAILED_TOAST);
  });

  it("verifies receipt SHA-256 inline and invokes New question handler", async () => {
    const user = userEvent.setup();
    const json = '{"schema":1}';
    const digestBuf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(json));
    const answerId = Array.from(new Uint8Array(digestBuf), (b) => b.toString(16).padStart(2, "0")).join("");

    const matchTurn = makeTurn({
      receipt: {
        answer_id: answerId,
        fact: makeFact(),
        json,
        exact: true,
      },
    });

    const onNewQuestion = vi.fn();
    renderTurn(matchTurn, { onNewQuestion });

    await user.click(screen.getByRole("button", { name: ACTION_VERIFY }));
    await waitFor(() => {
      expect(screen.getByText(RECEIPT_VERIFIED)).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: ACTION_NEW_QUESTION }));
    expect(onNewQuestion).toHaveBeenCalledTimes(1);

    cleanup();

    const mismatchTurn = makeTurn({
      receipt: {
        answer_id: "f".repeat(64),
        fact: makeFact(),
        json,
        exact: true,
      },
    });
    renderTurn(mismatchTurn);

    await user.click(screen.getByRole("button", { name: ACTION_VERIFY }));
    await waitFor(() => {
      expect(screen.getByText(RECEIPT_MISMATCH)).toBeInTheDocument();
    });
  });
});

describe("Refusal, clarify, and error cards", () => {
  it("renders refusal, clarify, and error cards with Retry re-sending the query", async () => {
    const user = userEvent.setup();
    const turn = makeTurn({
      sentences: [],
      windows: [],
      clarify: {
        type: "clarify",
        question: "Several instruments match. Which one do you mean?",
        candidates: [
          { coordinate: "uk/ukpga/1996/18/s124", label: "s.124 Employment Rights Act 1996" },
        ],
      },
      refusal: {
        type: "refusal",
        kind: "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND",
        message: "There is no such Act in the corpus.",
        suggestions: [{ coordinate: "uk/ukpga/1996/23", label: "Arbitration Act 1996" }],
        snapshot: "2026-09-28",
        link: null,
      },
      error: {
        source: "client",
        code: "network",
        message: "The connection failed.",
      },
    });

    const { onAsk } = renderTurn(turn);

    await user.click(screen.getByRole("button", { name: "s.124 Employment Rights Act 1996" }));
    expect(onAsk).toHaveBeenCalledWith("uk/ukpga/1996/18/s124");

    await user.click(screen.getByRole("button", { name: "Arbitration Act 1996" }));
    expect(onAsk).toHaveBeenCalledWith("uk/ukpga/1996/23");

    await user.click(screen.getByRole("button", { name: ACTION_RETRY }));
    expect(onAsk).toHaveBeenCalledWith(turn.query);
  });
});
