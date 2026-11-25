// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { initTheme } from "@/lib/theme";
import {
  ACTION_CHANGE,
  CLARIFY_ARIA_LABEL,
  CLARIFY_TITLE,
  COMPOSER_PLACEHOLDER,
  SOURCES_HEADING,
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

function stubPhoneViewport() {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 360 });
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
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
const TORT_CANDIDATES = [FIRST_CANDIDATE, SECOND_CANDIDATE];

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
        answer_id: `phone-${idSuffix}`.padEnd(64, "0"),
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
const originalInnerWidth = window.innerWidth;

beforeEach(() => {
  stubPhoneViewport();
  window.localStorage.clear();
  initTheme();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  globalThis.fetch = originalFetch;
  HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: originalInnerWidth,
  });
  document.documentElement.className = "";
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

describe("Clarify Change flow at phone width (360px)", () => {
  it("replaces turn in place, keeps original question, shows Using + Change, and re-opens choices", async () => {
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

    HTMLElement.prototype.scrollIntoView = vi.fn();

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);

    await user.type(textarea, "How long do I have to sue in tort?{Enter}");
    await screen.findByText(CLARIFY_TITLE);

    // Choose first candidate on phone layout.
    await user.click(screen.getByRole("button", { name: FIRST_CANDIDATE.label }));
    await screen.findByText("An action founded on tort must be brought within six years.");

    // Desktop sources column is absent; inline sources panel renders under the turn.
    expect(document.querySelector("[data-sources-column]")).toBeNull();
    expect(screen.getByRole("complementary", { name: SOURCES_HEADING })).toHaveTextContent(
      "Source text for s.2 Limitation Act 1980.",
    );

    // Replaced in place with original question kept and "Using ... Change" shown.
    const main = screen.getByRole("main");
    expect(main.querySelectorAll('article[id^="turn-"]')).toHaveLength(1);
    const headings = screen.getAllByRole("heading", { level: 2, name: /How long do I have to sue in tort\?/ });
    expect(headings).toHaveLength(1);
    expect(headings[0]).toHaveTextContent("How long do I have to sue in tort?");
    expect(headings[0]).not.toHaveTextContent("(s.2 Limitation Act 1980)");
    expect(screen.getByText(usingCitationText("s.2 Limitation Act 1980"))).toBeInTheDocument();

    // Change re-opens the choices with the active option disabled and selected.
    const changeBtn = screen.getByRole("button", { name: ACTION_CHANGE });
    await user.click(changeBtn);
    const list = screen.getByRole("list", { name: CLARIFY_ARIA_LABEL });
    const firstChip = within(list).getByRole("button", { name: FIRST_CANDIDATE.label });
    expect(firstChip).toBeDisabled();
    expect(firstChip).toHaveAttribute("aria-pressed", "true");

    // Pick second candidate and confirm in-place update.
    await user.click(within(list).getByRole("button", { name: SECOND_CANDIDATE.label }));
    await screen.findByText("A personal injury action has a three-year limit.");

    expect(sentQueries).toEqual([
      "How long do I have to sue in tort?",
      "How long do I have to sue in tort? (s.2 Limitation Act 1980)",
      "How long do I have to sue in tort? (s.11 Limitation Act 1980)",
    ]);
    expect(main.querySelectorAll('article[id^="turn-"]')).toHaveLength(1);
    expect(screen.getByText(usingCitationText("s.11 Limitation Act 1980"))).toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: SOURCES_HEADING })).toHaveTextContent(
      "Source text for s.11 Limitation Act 1980.",
    );
  });
});
