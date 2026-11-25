// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatAnswerCopyText } from "@/components/ActionRow";
import { isQuoteAnswer, type ReplaceRequest, Turn } from "@/components/Turn";
import { ToastProvider } from "@/components/ui/toast";
import type { AnswerFactObject, Window } from "@/api/events";
import type { Turn as TurnData } from "@/state/reducer";
import {
  ACTION_COPY,
  ACTION_DETAILS,
  ACTION_DOWNLOAD_EVIDENCE,
  ACTION_RETRY,
  COPIED_TOAST,
  COPY_FAILED_TOAST,
  EVIDENCE_DOWNLOADED_TOAST,
  EVIDENCE_MISMATCH,
  EXTRACTIVE_REASON,
  quoteLinkLabel,
  shortId,
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
    onReplace?: (req: ReplaceRequest) => void;
    onShowSource?: (turnId: number, passageId: string) => void;
  } = {},
) {
  const onReplace = handlers.onReplace ?? vi.fn();
  const onShowSource = handlers.onShowSource ?? vi.fn();
  const result = render(
    <ToastProvider>
      <Turn
        turn={turn}
        busy={handlers.busy ?? false}
        onReplace={onReplace}
        onShowSource={onShowSource}
      />
    </ToastProvider>,
  );
  return { ...result, onReplace, onShowSource };
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
        <Turn turn={step1} busy={true} onReplace={vi.fn()} onShowSource={vi.fn()} />
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
        <Turn turn={step2} busy={true} onReplace={vi.fn()} onShowSource={vi.fn()} />
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
        <Turn turn={streamingWithSentence} busy={true} onReplace={vi.fn()} onShowSource={vi.fn()} />
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
  it("renders full text, icon link, and per-block copy button for a single quote_requested window", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    const w1 = makeWindow({
      passage_id: "P1",
      coordinate: "uk/ukpga/2006/46/s172/1",
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

    expect(isQuoteAnswer(turn)).toBe(true);
    expect(screen.getByText(w1.heading)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3 })).not.toBeInTheDocument();
    expect(screen.getByText(w1.text)).toBeInTheDocument();

    const extLink = screen.getByRole("link", { name: quoteLinkLabel(w1.heading) });
    expect(extLink).toHaveAttribute("href", w1.source_url);
    expect(extLink.textContent).toBe("");
    expect(screen.queryByText(SOURCE_LINK_LABEL)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: w1.heading })).not.toBeInTheDocument();

    const copyBtn = screen.getByRole("button", { name: `Copy ${w1.heading}` });
    await user.click(copyBtn);

    expect(writeText).toHaveBeenCalledWith(`${w1.heading}\n${w1.text}\n${w1.source_url}`);
    expect(screen.getByRole("status")).toHaveTextContent(COPIED_TOAST);
  });

  it("groups sibling quote windows under one common heading with short block labels and icon links", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    const w1 = makeWindow({
      passage_id: "P1",
      coordinate: "uk/ukpga/2006/46/s172/1/a",
      heading: "Companies Act 2006, section 172(1)(a) (Duty to promote the success of the company)",
      text: "(a) the likely consequences of any decision in the long term,",
      source_url: "https://www.legislation.gov.uk/ukpga/2006/46/section/172/1/a",
    });
    const w2 = makeWindow({
      passage_id: "P2",
      coordinate: "uk/ukpga/2006/46/s172/1/b",
      heading: "Companies Act 2006, section 172(1)(b) (Duty to promote the success of the company)",
      text: "(b) the interests of the company’s employees,",
      source_url: "https://www.legislation.gov.uk/ukpga/2006/46/section/172/1/b",
    });

    const turn = makeTurn({
      query: "Quote section 172(1) of the Companies Act 2006",
      sentences: [],
      windows: [w1, w2],
      extractive: {
        type: "extractive",
        reason: "quote_requested",
        windows: [w1, w2],
      },
    });

    renderTurn(turn);

    expect(screen.getByRole("heading", { level: 3, name: "Companies Act 2006, section 172(1)" })).toBeInTheDocument();
    expect(screen.queryByText("(a)")).not.toBeInTheDocument();
    expect(screen.queryByText("(b)")).not.toBeInTheDocument();
    expect(screen.getByText(w1.text)).toBeInTheDocument();
    expect(screen.getByText(w2.text)).toBeInTheDocument();
    expect(screen.queryByText(w1.heading)).not.toBeInTheDocument();
    expect(screen.queryByText(w2.heading)).not.toBeInTheDocument();
    expect(screen.queryByText(SOURCE_LINK_LABEL)).not.toBeInTheDocument();

    const linkA = screen.getByRole("link", { name: quoteLinkLabel("(a)") });
    const linkB = screen.getByRole("link", { name: quoteLinkLabel("(b)") });
    expect(linkA).toHaveAttribute("href", w1.source_url);
    expect(linkA.textContent).toBe("");
    expect(linkB).toHaveAttribute("href", w2.source_url);
    expect(linkB.textContent).toBe("");

    const copyA = screen.getByRole("button", { name: "Copy (a)" });
    expect(screen.getByRole("button", { name: "Copy (b)" })).toBeInTheDocument();
    expect(copyA.closest("blockquote")?.querySelector("p")).toHaveTextContent(w1.text);
    expect(copyA.parentElement?.parentElement).toHaveClass("flex", "items-start", "justify-between");
    expect(copyA.parentElement?.parentElement).toContainElement(screen.getByText(w1.text));

    await user.click(copyA);
    expect(writeText).toHaveBeenCalledWith(`${w1.heading}\n${w1.text}\n${w1.source_url}`);
  });

  it("keeps the separate block label in a grouped quote when the text does not start with that label", () => {
    const w1 = makeWindow({
      passage_id: "P1",
      coordinate: "uk/ukpga/2006/46/s172/1",
      heading: "Companies Act 2006, section 172(1) (Duty to promote the success of the company)",
      text: "(1) A director of a company must act in the way he considers, in good faith, would be most likely to promote the success of the company.",
      source_url: "https://www.legislation.gov.uk/ukpga/2006/46/section/172/1",
    });
    const w2 = makeWindow({
      passage_id: "P2",
      coordinate: "uk/ukpga/2006/46/s172/2/a",
      heading: "Companies Act 2006, section 172(2)(a) (Duty to promote the success of the company)",
      text: "(a) if or to the extent that the purposes of the company consist of or include purposes other than the benefit of its members,",
      source_url: "https://www.legislation.gov.uk/ukpga/2006/46/section/172/2/a",
    });

    const turn = makeTurn({
      query: "Quote section 172 of the Companies Act 2006",
      sentences: [],
      windows: [w1, w2],
      extractive: {
        type: "extractive",
        reason: "quote_requested",
        windows: [w1, w2],
      },
    });

    renderTurn(turn);

    expect(screen.getByRole("heading", { level: 3, name: "Companies Act 2006, section 172" })).toBeInTheDocument();
    expect(screen.queryByText("(1)")).not.toBeInTheDocument();
    expect(screen.getByText("(2)(a)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy (1)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: quoteLinkLabel("(1)") })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy (2)(a)" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: quoteLinkLabel("(2)(a)") })).toBeInTheDocument();
  });

  it("omits group heading and uses short coordinate labels when quote windows do not share a parent", () => {
    const w1 = makeWindow({
      passage_id: "P1",
      coordinate: "uk/ukpga/2006/46/s172/1/a",
      heading: "Companies Act 2006, section 172(1)(a) (Duty to promote the success of the company)",
      text: "(a) the likely consequences of any decision in the long term,",
      source_url: "https://www.legislation.gov.uk/ukpga/2006/46/section/172/1/a",
    });
    const w2 = makeWindow({
      passage_id: "P2",
      coordinate: "uk/ukpga/2006/46/s174/1",
      heading: "Companies Act 2006, section 174(1) (Duty to exercise reasonable care, skill and diligence)",
      text: "(1) A director of a company must exercise reasonable care, skill and diligence.",
      source_url: "https://www.legislation.gov.uk/ukpga/2006/46/section/174/1",
    });

    const turn = makeTurn({
      sentences: [],
      windows: [w1, w2],
      extractive: {
        type: "extractive",
        reason: "quote_requested",
        windows: [w1, w2],
      },
    });

    renderTurn(turn);

    expect(screen.queryByRole("heading", { level: 3 })).not.toBeInTheDocument();
    expect(screen.getByText("s.172(1)(a)")).toBeInTheDocument();
    expect(screen.getByText("s.174(1)")).toBeInTheDocument();
  });

  it("renders notice, jump link, and QuoteBlock without per-block copy for other extractive reasons", async () => {
    const user = userEvent.setup();
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

    const { onShowSource } = renderTurn(turn);

    expect(isQuoteAnswer(turn)).toBe(false);
    expect(isQuoteAnswer(makeTurn())).toBe(false);
    expect(screen.getByText(EXTRACTIVE_REASON.providers_unavailable)).toBeInTheDocument();
    expect(screen.getByText(w1.text)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: `Copy ${w1.heading}` })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: quoteLinkLabel(w1.heading) })).toHaveAttribute("href", w1.source_url);

    await user.click(screen.getByRole("link", { name: w1.heading }));
    expect(onShowSource).toHaveBeenCalledWith(1, "P1");
  });
});

describe("ActionRow", () => {
  it("appears only when turn phase is done", () => {
    const streaming = makeTurn({ phase: "streaming", receipt: null, timings: null });
    const { rerender } = renderTurn(streaming);

    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DOWNLOAD_EVIDENCE })).not.toBeInTheDocument();

    const failed = makeTurn({
      phase: "failed",
      receipt: null,
      timings: null,
      error: { source: "client", code: "network", message: "The connection failed." },
    });
    rerender(
      <ToastProvider>
        <Turn turn={failed} busy={false} onReplace={vi.fn()} onShowSource={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();

    const done = makeTurn({ phase: "done" });
    rerender(
      <ToastProvider>
        <Turn turn={done} busy={false} onReplace={vi.fn()} onShowSource={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.getByRole("button", { name: ACTION_COPY })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: ACTION_DOWNLOAD_EVIDENCE })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new question/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /verify/i })).not.toBeInTheDocument();
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

  it("downloads evidence JSON with short id filename, toasts on match, and keeps mismatch caption visible", async () => {
    const user = userEvent.setup();
    const json = '{"schema":1}';
    const digestBuf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(json));
    const answerId = Array.from(new Uint8Array(digestBuf), (b) => b.toString(16).padStart(2, "0")).join("");

    const downloadedFiles: string[] = [];
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:evidence-test"),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloadedFiles.push(this.download);
    });

    try {
      const matchTurn = makeTurn({
        receipt: {
          answer_id: answerId,
          fact: makeFact(),
          json,
          exact: true,
        },
      });

      renderTurn(matchTurn);

      await user.click(screen.getByRole("button", { name: ACTION_DOWNLOAD_EVIDENCE }));
      expect(downloadedFiles).toEqual([`chapter-and-verse-evidence-${shortId(answerId)}.json`]);
      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(EVIDENCE_DOWNLOADED_TOAST);
      });
      expect(screen.queryByText(EVIDENCE_MISMATCH)).not.toBeInTheDocument();
      expect(screen.queryByText(/verif/i)).not.toBeInTheDocument();

      cleanup();

      const mismatchId = "f".repeat(64);
      const mismatchTurn = makeTurn({
        receipt: {
          answer_id: mismatchId,
          fact: makeFact(),
          json,
          exact: true,
        },
      });
      renderTurn(mismatchTurn);

      await user.click(screen.getByRole("button", { name: ACTION_DOWNLOAD_EVIDENCE }));
      expect(downloadedFiles).toEqual([
        `chapter-and-verse-evidence-${shortId(answerId)}.json`,
        `chapter-and-verse-evidence-${shortId(mismatchId)}.json`,
      ]);
      await waitFor(() => {
        expect(screen.getByText(EVIDENCE_MISMATCH)).toBeInTheDocument();
      });
      const mismatchCaption = screen.getByText(EVIDENCE_MISMATCH).closest("span[aria-live]");
      expect(mismatchCaption).toHaveClass("text-muted-foreground");
      expect(mismatchCaption?.querySelector("svg")).toBeInTheDocument();
      expect(screen.getByRole("status")).not.toHaveTextContent(EVIDENCE_DOWNLOADED_TOAST);
      expect(screen.queryByText(/verif/i)).not.toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("Refusal, clarify, and error cards", () => {
  it("replaces in place with composed query on clarify/refusal choices and same payload on Retry, with no action row", async () => {
    const user = userEvent.setup();
    const candidates = [
      { coordinate: "uk/ukpga/1996/18/s124", label: "s.124 Employment Rights Act 1996: Limit of award" },
    ];
    const turn = makeTurn({
      query: "What is the limit?",
      question: "What is the limit?",
      sentences: [],
      windows: [],
      clarify: {
        type: "clarify",
        question: "Several instruments match. Which one do you mean?",
        candidates,
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

    const { onReplace } = renderTurn(turn);

    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DOWNLOAD_EVIDENCE })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DETAILS })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "s.124 Employment Rights Act 1996: Limit of award" }));
    expect(onReplace).toHaveBeenNthCalledWith(1, {
      oldTurnId: 1,
      query: "What is the limit? (s.124 Employment Rights Act 1996)",
      question: "What is the limit?",
      choice: {
        citation: "s.124 Employment Rights Act 1996",
        alternatives: candidates,
      },
    });

    await user.click(screen.getByRole("button", { name: "Arbitration Act 1996" }));
    expect(onReplace).toHaveBeenNthCalledWith(2, {
      oldTurnId: 1,
      query: "What is the limit? (Arbitration Act 1996)",
      question: "What is the limit?",
      choice: {
        citation: "Arbitration Act 1996",
        alternatives: [],
      },
    });

    await user.click(screen.getByRole("button", { name: ACTION_RETRY }));
    expect(onReplace).toHaveBeenNthCalledWith(3, {
      oldTurnId: 1,
      query: "What is the limit?",
      question: "What is the limit?",
      choice: null,
    });
  });
});
