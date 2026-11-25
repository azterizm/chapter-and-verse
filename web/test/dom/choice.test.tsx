// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { initTheme } from "@/lib/theme";
import {
  ACTION_CHANGE,
  ACTION_COPY,
  ACTION_DETAILS,
  ACTION_DOWNLOAD_EVIDENCE,
  ACTION_RETRY,
  CLARIFY_ARIA_LABEL,
  CLARIFY_TITLE,
  COMPOSER_PLACEHOLDER,
  ERROR_SERVER_TITLE,
  usingCitationText,
} from "@/text";

type SseItem = { event: string; data: unknown };

function sseResponse(events: SseItem[]): Response {
  const payload = events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join("");
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function stubMatchMedia(desktop = true) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("min-width: 1024px") ? desktop : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

const FIRST_CANDIDATE = {
  coordinate: "uk/ukpga/1980/58/s2",
  label: "s.2 Limitation Act 1980: Time limit for actions founded on tort",
};
const SECOND_CANDIDATE = {
  coordinate: "uk/ukpga/1980/58/s11",
  label: "s.11 Limitation Act 1980: Special time limit for actions in respect of personal injuries",
};
const THIRD_CANDIDATE = {
  coordinate: "uk/ukpga/1980/58/s14A",
  label: "s.14A Limitation Act 1980: Special time limit for negligence actions",
};
const TORT_CANDIDATES = [FIRST_CANDIDATE, SECOND_CANDIDATE, THIRD_CANDIDATE];

function answerEvents(idSuffix: string, coord: string, label: string, text: string): SseItem[] {
  const pid = `P-${idSuffix}`;
  return [
    {
      event: "route",
      data: {
        type: "route",
        status: "ROUTE_BOUNDED",
        next_action: "RETRIEVE_BOUNDED",
        coordinates: [coord],
        corrections: [],
        messages: [],
        index_snapshot: "2026-09-28",
      },
    },
    {
      event: "sources",
      data: {
        type: "sources",
        windows: [
          {
            passage_id: pid,
            coordinate: coord,
            heading: `Limitation Act 1980, ${label.split(" ")[0]}`,
            status: "IN_FORCE",
            version_date: "1991-02-01",
            text: `Source text for ${label}.`,
            highlight: null,
            source_url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
          },
        ],
      },
    },
    {
      event: "sentence",
      data: {
        type: "sentence",
        sentence: {
          index: 0,
          text,
          verdict: "supported",
          passage_ids: [pid],
          reasons: ["GROUNDED"],
          rollbacks: 0,
        },
        citations: [
          {
            passage_id: pid,
            coordinate: coord,
            label,
            url: "https://www.legislation.gov.uk/ukpga/1980/58/section/2",
          },
        ],
      },
    },
    {
      event: "receipt",
      data: {
        type: "receipt",
        answer_id: `ans-${idSuffix}`.padEnd(64, "0"),
        fact: {
          schema: 1,
          mode: "generated",
          corpus_id: "ukpga",
          policy_hash: "ph",
          query_sha256: "qh",
          route_status: "ROUTE_BOUNDED",
          coordinates: [coord],
          passages: { [pid]: coord },
          version_dates: {},
          spans: {},
          sentences: [],
          calls: [],
        },
      },
    },
    {
      event: "done",
      data: {
        type: "done",
        timings: { first_source_ms: 10, first_sentence_ms: 20, total_ms: 30 },
      },
    },
  ];
}

const originalFetch = globalThis.fetch;
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;

beforeEach(() => {
  stubMatchMedia(true);
  window.localStorage.clear();
  initTheme();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  globalThis.fetch = originalFetch;
  HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  document.documentElement.className = "";
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

describe("Choice, Change, Retry, and Conversation State (S1-S7)", () => {
  it("replaces clarify turn in place with original heading, Using line, composed query, no coordinates, no action row on clarify, and updates sources and focus", async () => {
    const user = userEvent.setup();
    const sentQueries: string[] = [];

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        const body = JSON.parse(String(init?.body ?? "{}")) as { query: string };
        sentQueries.push(body.query);

        if (body.query === "How long do I have to sue in tort?") {
          return sseResponse([
            {
              event: "route",
              data: {
                type: "route",
                status: "ROUTE_AMBIGUOUS",
                next_action: "ASK_USER",
                coordinates: [],
                corrections: [],
                messages: [],
                index_snapshot: "2026-09-28",
              },
            },
            {
              event: "clarify",
              data: {
                type: "clarify",
                question: "Several provisions match. Which one do you mean?",
                candidates: TORT_CANDIDATES,
              },
            },
            {
              event: "done",
              data: {
                type: "done",
                timings: { first_source_ms: null, first_sentence_ms: null, total_ms: 15 },
              },
            },
          ]);
        }

        if (body.query === "How long do I have to sue in tort? (s.2 Limitation Act 1980)") {
          return sseResponse(
            answerEvents(
              "s2",
              "uk/ukpga/1980/58/s2",
              "s.2 Limitation Act 1980",
              "An action founded on tort must be brought within six years.",
            ),
          );
        }

        return sseResponse(
          answerEvents(
            "s11",
            "uk/ukpga/1980/58/s11",
            "s.11 Limitation Act 1980",
            "A personal injury action has a three-year limit.",
          ),
        );
      }
      return new Response("Not found", { status: 404 });
    });

    const scrollSpy = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollSpy;

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);

    // Ask ambiguous question
    await user.type(textarea, "How long do I have to sue in tort?{Enter}");
    await screen.findByText(CLARIFY_TITLE);

    // Clarify turn has no action row or Details button, and no coordinates in DOM
    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DOWNLOAD_EVIDENCE })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DETAILS })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("uk/ukpga/");

    // Choose first candidate
    await user.click(screen.getByRole("button", { name: FIRST_CANDIDATE.label }));

    await screen.findByText("An action founded on tort must be brought within six years.");

    // Sent query is composed text
    expect(sentQueries).toEqual([
      "How long do I have to sue in tort?",
      "How long do I have to sue in tort? (s.2 Limitation Act 1980)",
    ]);

    // Replaced in place: only one turn article, original question heading, Using line, no clarify card
    const headings = screen.getAllByRole("heading", { level: 2, name: /How long do I have to sue in tort\?/ });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("How long do I have to sue in tort?");
    expect(headings[0]).not.toHaveTextContent("(s.2 Limitation Act 1980)");
    expect(screen.queryByText(CLARIFY_TITLE)).not.toBeInTheDocument();
    expect(screen.getByText(usingCitationText("s.2 Limitation Act 1980"))).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("uk/ukpga/");

    // Sources column shows the new turn's sources
    const sourcesCol = document.querySelector("[data-sources-column]");
    expect(sourcesCol).not.toBeNull();
    expect(sourcesCol).toHaveTextContent("Source text for s.2 Limitation Act 1980.");

    // Focus moved to the new turn's heading region (not the composer) and scrollIntoView called
    await waitFor(() => {
      expect(headings[0]).toHaveFocus();
    });
    expect(textarea).not.toHaveFocus();
    expect(scrollSpy).toHaveBeenCalled();

    // S2: Change opens choice list with current option selected & disabled; Escape and second Change close it
    const changeBtn = screen.getByRole("button", { name: ACTION_CHANGE });
    expect(changeBtn).toHaveAttribute("aria-expanded", "false");

    await user.click(changeBtn);
    expect(changeBtn).toHaveAttribute("aria-expanded", "true");
    const list = screen.getByRole("list", { name: CLARIFY_ARIA_LABEL });
    const currentChip = within(list).getByRole("button", { name: FIRST_CANDIDATE.label });
    expect(currentChip).toBeDisabled();
    expect(currentChip).toHaveAttribute("aria-pressed", "true");
    expect(currentChip).toHaveAttribute("data-selected", "true");

    // Second Change closes list
    await user.click(changeBtn);
    expect(screen.queryByRole("list", { name: CLARIFY_ARIA_LABEL })).not.toBeInTheDocument();

    // Reopen and press Escape to close
    await user.click(changeBtn);
    expect(screen.getByRole("list", { name: CLARIFY_ARIA_LABEL })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("list", { name: CLARIFY_ARIA_LABEL })).not.toBeInTheDocument();
    expect(changeBtn).toHaveFocus();

    // Reopen and choose second candidate: replaces again without nested brackets
    await user.click(changeBtn);
    await user.click(screen.getByRole("button", { name: SECOND_CANDIDATE.label }));

    await screen.findByText("A personal injury action has a three-year limit.");

    expect(sentQueries).toEqual([
      "How long do I have to sue in tort?",
      "How long do I have to sue in tort? (s.2 Limitation Act 1980)",
      "How long do I have to sue in tort? (s.11 Limitation Act 1980)",
    ]);
    expect(screen.getAllByRole("heading", { level: 2, name: /How long do I have to sue in tort\?/ })).toHaveLength(1);
    expect(screen.getByText(usingCitationText("s.11 Limitation Act 1980"))).toBeInTheDocument();
    expect(sourcesCol).toHaveTextContent("Source text for s.11 Limitation Act 1980.");
    expect(document.body.textContent).not.toContain("uk/ukpga/");
  });

  it("renders exactly one divider between two consecutive turns and none after the last turn", async () => {
    const user = userEvent.setup();
    let count = 0;

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        count += 1;
        return sseResponse(
          answerEvents(
            String(count),
            `uk/ukpga/1980/58/s${count}`,
            `s.${count} Limitation Act 1980`,
            `Answer ${count}.`,
          ),
        );
      }
      return new Response("Not found", { status: 404 });
    });

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);

    await user.type(textarea, "First question{Enter}");
    await screen.findByText("Answer 1.");
    await user.type(textarea, "Second question{Enter}");
    await screen.findByText("Answer 2.");

    const main = screen.getByRole("main");
    const turnArticles = main.querySelectorAll('article[id^="turn-"]');
    expect(turnArticles).toHaveLength(2);
    for (const article of turnArticles) {
      expect(article).not.toHaveClass("border-b");
    }

    const wrappers = Array.from(turnArticles).map((a) => a.parentElement as HTMLElement);
    expect(wrappers[0]).toHaveClass("border-b", "last:border-b-0");
    expect(wrappers[1]).toHaveClass("border-b", "last:border-b-0");
    // Only the wrapper divs hold border-b inside the turns list
    expect(main.querySelectorAll(".border-b")).toHaveLength(2);
  });

  it("Retry on an error card replaces the turn in place without duplicating headings and has no action row", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    const sentQueries: string[] = [];

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        attempt += 1;
        const body = JSON.parse(String(init?.body ?? "{}")) as { query: string };
        sentQueries.push(body.query);

        if (attempt === 1) {
          return sseResponse([
            {
              event: "error",
              data: { type: "error", code: "server_error", message: "Temporary outage." },
            },
            {
              event: "done",
              data: { type: "done", timings: { first_source_ms: null, first_sentence_ms: null, total_ms: 10 } },
            },
          ]);
        }
        return sseResponse(
          answerEvents(
            "retry",
            "uk/ukpga/1980/58/s2",
            "s.2 Limitation Act 1980",
            "Recovered answer after retry.",
          ),
        );
      }
      return new Response("Not found", { status: 404 });
    });

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);

    await user.type(textarea, "How long do I have to sue in tort?{Enter}");
    await screen.findByText(ERROR_SERVER_TITLE);

    // Error turn has no action row
    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DETAILS })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: ACTION_RETRY }));
    await screen.findByText("Recovered answer after retry.");

    expect(sentQueries).toEqual([
      "How long do I have to sue in tort?",
      "How long do I have to sue in tort?",
    ]);
    expect(screen.getAllByRole("heading", { level: 2, name: /How long do I have to sue in tort\?/ })).toHaveLength(1);
    expect(screen.queryByText(ERROR_SERVER_TITLE)).not.toBeInTheDocument();
  });

  it("replaces a refusal turn in place when choosing a suggestion without displaying coordinates or Change for a single suggestion", async () => {
    const user = userEvent.setup();
    const sentQueries: string[] = [];

    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        const body = JSON.parse(String(init?.body ?? "{}")) as { query: string };
        sentQueries.push(body.query);

        if (body.query === "What does Marchwood Arbitration Act say?") {
          return sseResponse([
            {
              event: "route",
              data: {
                type: "route",
                status: "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND",
                next_action: "REFUSE",
                coordinates: [],
                corrections: [],
                messages: [],
                index_snapshot: "2026-09-28",
              },
            },
            {
              event: "refusal",
              data: {
                type: "refusal",
                kind: "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND",
                message: "No such Act.",
                suggestions: [{ coordinate: "uk/ukpga/1996/23", label: "Arbitration Act 1996" }],
                snapshot: "2026-09-28",
                link: null,
              },
            },
            {
              event: "receipt",
              data: {
                type: "receipt",
                answer_id: "b".repeat(64),
                fact: {
                  schema: 1,
                  mode: "refusal",
                  corpus_id: "ukpga",
                  policy_hash: "ph",
                  query_sha256: "qh",
                  route_status: "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND",
                  coordinates: [],
                  passages: {},
                  version_dates: {},
                  spans: {},
                  sentences: [],
                  calls: [],
                },
              },
            },
            {
              event: "done",
              data: { type: "done", timings: { first_source_ms: null, first_sentence_ms: null, total_ms: 10 } },
            },
          ]);
        }

        return sseResponse(
          answerEvents(
            "arb",
            "uk/ukpga/1996/23/s3",
            "s.3 Arbitration Act 1996",
            "The seat of the arbitration is determined by the parties.",
          ),
        );
      }
      return new Response("Not found", { status: 404 });
    });

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);

    await user.type(textarea, "What does Marchwood Arbitration Act say?{Enter}");
    await screen.findByText("No such Act.");

    // Refusal turn has no action row even though it has a receipt
    expect(screen.queryByRole("button", { name: ACTION_COPY })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DOWNLOAD_EVIDENCE })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_DETAILS })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Arbitration Act 1996" }));
    await screen.findByText("The seat of the arbitration is determined by the parties.");

    expect(sentQueries).toEqual([
      "What does Marchwood Arbitration Act say?",
      "What does Marchwood Arbitration Act say? (Arbitration Act 1996)",
    ]);
    expect(screen.getAllByRole("heading", { level: 2, name: /What does Marchwood Arbitration Act say\?/ })).toHaveLength(
      1,
    );
    expect(screen.getByText(usingCitationText("Arbitration Act 1996"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: ACTION_CHANGE })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("uk/ukpga/");
  });
});
