// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { Footer } from "@/components/Footer";
import { QueryBox } from "@/components/QueryBox";
import { initTheme, THEME_STORAGE_KEY } from "@/lib/theme";
import {
  COMPOSER_PLACEHOLDER,
  EXAMPLE_QUESTIONS,
  EXAMPLES_LABEL,
  FOOTER_NO_PERSONAL_DATA,
  FOOTER_NOT_ADVICE,
  FOOTER_OGL,
  MEMONSYSTEMS_ARIA_LABEL,
  MEMONSYSTEMS_LABEL,
  MEMONSYSTEMS_URL,
  PURPOSE_LINE,
  SEND_LABEL,
  SOURCES_HEADING,
  START_OVER_LABEL,
  THEME_DARK,
  THEME_LABEL,
  THEME_LIGHT,
  THEME_SYSTEM,
  WORDMARK,
} from "@/text";

function stubMatchMedia(options: { dark?: boolean; desktop?: boolean } = {}) {
  const dark = options.dark ?? false;
  const desktop = options.desktop ?? false;
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("min-width: 1024px")
        ? desktop
        : query.includes("prefers-color-scheme: dark")
          ? dark
          : false,
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

function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const payload = events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
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

const originalFetch = globalThis.fetch;

beforeEach(() => {
  stubMatchMedia();
  window.localStorage.clear();
  initTheme();
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/health")) {
      return new Response(
        JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response("Not found", { status: 404 });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  globalThis.fetch = originalFetch;
  document.documentElement.className = "";
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

describe("App empty state and shell", () => {
  it("shows the wordmark, purpose line, composer, and exactly the three examples", () => {
    render(<App />);

    expect(screen.getByText(WORDMARK)).toBeInTheDocument();
    expect(screen.getByText(PURPOSE_LINE)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(COMPOSER_PLACEHOLDER)).toBeInTheDocument();

    const list = screen.getByRole("list", { name: EXAMPLES_LABEL });
    const buttons = within(list).getAllByRole("button");
    expect(buttons).toHaveLength(3);
    expect(buttons.map((b) => b.textContent)).toEqual([...EXAMPLE_QUESTIONS]);

    // Flex column shell: min-h-dvh, main grows, hero + composer form one centred group (~40% from top).
    const mainEl = screen.getByRole("main");
    const shell = mainEl.closest(".min-h-dvh");
    expect(shell).toHaveClass("flex", "flex-col");
    expect(mainEl).toHaveClass("flex", "w-full", "flex-1", "flex-col");

    const topSpacer = mainEl.querySelector('[data-empty-spacer="top"]');
    const bottomSpacer = mainEl.querySelector('[data-empty-spacer="bottom"]');
    const hero = mainEl.querySelector("[data-hero]");
    const dock = mainEl.querySelector("[data-composer-dock]");

    expect(topSpacer).toHaveClass("flex-[2]");
    expect(bottomSpacer).toHaveClass("flex-[3]");
    expect(hero).not.toBeNull();
    expect(hero).toContainElement(screen.getByText(WORDMARK));
    expect(hero).toContainElement(screen.getByText(PURPOSE_LINE));
    expect(hero).toContainElement(list);

    expect(dock).not.toBeNull();
    expect(dock).toHaveClass("composer-motion");
    expect(dock).not.toHaveClass("sticky", "bottom-0", "mt-auto");
    expect(mainEl.closest(".max-w-\\[728px\\]")).not.toBeNull();
  });

  it("moves the composer to the bottom dock and fades out the hero on the first question", async () => {
    const user = userEvent.setup();
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        return sseResponse([
          {
            event: "done",
            data: { type: "done", timings: { first_source_ms: null, first_sentence_ms: null, total_ms: 10 } },
          },
        ]);
      }
      return new Response("Not found", { status: 404 });
    });

    const rectSpy = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this.hasAttribute("data-hero")) {
        return { top: 180, left: 0, right: 680, bottom: 340, width: 680, height: 160, x: 0, y: 180, toJSON() {} };
      }
      if (this.hasAttribute("data-composer-dock")) {
        const docked = this.classList.contains("sticky");
        const top = docked ? 700 : 360;
        return { top, left: 0, right: 680, bottom: top + 64, width: 680, height: 64, x: 0, y: top, toJSON() {} };
      }
      if (this.id === "main") {
        const docked = this.querySelector("[data-composer-dock]")?.classList.contains("sticky");
        const top = docked ? 73 : 0;
        return { top, left: 0, right: 680, bottom: 760, width: 680, height: 760 - top, x: 0, y: top, toJSON() {} };
      }
      return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON() {} };
    });

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);
    await user.type(textarea, "First question{Enter}");

    const mainEl = screen.getByRole("main");
    const dock = mainEl.querySelector("[data-composer-dock]") as HTMLElement;
    const hero = mainEl.querySelector("[data-hero]") as HTMLElement;

    expect(dock).toHaveClass("composer-motion", "sticky", "bottom-0", "mt-auto");
    expect(dock.style.transform).toMatch(/^translateY\(0(?:px)?\)$/);
    fireEvent.transitionEnd(dock);
    expect(dock.style.transform).toBe("");

    expect(hero).toHaveClass("hero-fade-out", "pointer-events-none", "absolute");
    expect(hero).toHaveAttribute("aria-hidden", "true");
    expect(hero.style.top).toBe("107px");
    await waitFor(() => {
      expect(mainEl.querySelector("[data-hero]")).toBeNull();
    });
    expect(mainEl.querySelector("[data-empty-spacer]")).toBeNull();

    rectSpy.mockRestore();
  });

  it("fills and focuses the composer when an example is clicked without sending", async () => {
    const user = userEvent.setup();
    render(<App />);

    const firstExample = EXAMPLE_QUESTIONS[0] ?? "";
    const chip = screen.getByRole("button", { name: firstExample });
    await user.click(chip);

    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);
    expect(textarea).toHaveValue(firstExample);
    expect(textarea).toHaveFocus();

    const askCalls = vi
      .mocked(globalThis.fetch)
      .mock.calls.filter(([url]) => String(url).endsWith("/api/ask"));
    expect(askCalls).toHaveLength(0);
    expect(screen.getByRole("list", { name: EXAMPLES_LABEL })).toBeInTheDocument();
  });

  it("renders sticky sources column on desktop for active answer and inline sources for earlier answers", async () => {
    stubMatchMedia({ desktop: true });
    const user = userEvent.setup();

    let askCount = 0;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        askCount += 1;
        const pid = `p-${askCount}`;
        return sseResponse([
          {
            event: "route",
            data: {
              type: "route",
              status: "ROUTE_BOUNDED",
              next_action: "synthesize",
              coordinates: [],
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
                  heading: `Heading ${askCount}`,
                  coordinate: `ukpga/2006/46/section/${askCount}`,
                  status: "IN_FORCE",
                  version_date: "2026-09-28",
                  text: `Provision text ${askCount}`,
                  highlight: null,
                  source_url: `https://www.legislation.gov.uk/ukpga/2006/46/section/${askCount}`,
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
                passage_ids: [pid],
                reasons: ["GROUNDED"],
                rollbacks: 0,
                text: `Answer sentence ${askCount}.`,
                verdict: "supported",
              },
              citations: [
                {
                  coordinate: `ukpga/2006/46/section/${askCount}`,
                  label: `s.${askCount} Companies Act 2006`,
                  passage_id: pid,
                  url: `https://www.legislation.gov.uk/ukpga/2006/46/section/${askCount}`,
                },
              ],
            },
          },
          {
            event: "receipt",
            data: {
              type: "receipt",
              answer_id: `ans-${askCount}`,
              fact: {
                schema: 1,
                mode: "generated",
                corpus_id: "ukpga",
                policy_hash: "ph",
                query_sha256: "qh",
                route_status: "ROUTE_BOUNDED",
                coordinates: [],
                passages: { [pid]: `ukpga/2006/46/section/${askCount}` },
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
        ]);
      }
      return new Response("Not found", { status: 404 });
    });

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);

    await user.type(textarea, "First question{Enter}");
    await waitFor(() => {
      expect(document.getElementById("w-1-p-1")).toBeInTheDocument();
    });

    const mainEl = screen.getByRole("main");
    const firstWindow = document.getElementById("w-1-p-1");
    expect(firstWindow).not.toBeNull();
    // Active turn's sources are outside <main> in the sticky column on desktop.
    expect(mainEl.contains(firstWindow)).toBe(false);

    await user.type(textarea, "Second question{Enter}");
    await waitFor(() => {
      expect(document.getElementById("w-2-p-2")).toBeInTheDocument();
    });

    const earlierWindow = document.getElementById("w-1-p-1");
    const activeWindow = document.getElementById("w-2-p-2");
    expect(earlierWindow).not.toBeNull();
    expect(activeWindow).not.toBeNull();
    // Earlier answer's sources move inline inside <main>; active answer stays in sticky column.
    expect(mainEl.contains(earlierWindow)).toBe(true);
    expect(mainEl.contains(activeWindow)).toBe(false);

    // Desktop layout keeps 680px conversation + 32px gap + 360px sticky sources, with growing main.
    const gridEl = mainEl.parentElement;
    expect(gridEl).toHaveClass("lg:grid-cols-[minmax(0,680px)_360px]", "lg:gap-8");
    expect(gridEl).not.toHaveClass("lg:items-start");
    expect(mainEl.closest(".lg\\:max-w-\\[1120px\\]")).not.toBeNull();

    const sourcesCol = activeWindow?.closest("[data-sources-column]");
    expect(sourcesCol).toHaveClass("lg:sticky", "lg:self-start");
  });

  it("renders no sources and keeps the conversation centred for a quote answer", async () => {
    stubMatchMedia({ desktop: true });
    const user = userEvent.setup();

    let askCount = 0;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        askCount += 1;
        const pid = `p-${askCount}`;
        const win = {
          passage_id: pid,
          heading: `Companies Act 2006, section ${askCount}`,
          coordinate: `uk/ukpga/2006/46/s${askCount}`,
          status: "IN_FORCE",
          version_date: "2026-09-28",
          text: `Statutory provision text ${askCount}.`,
          highlight: null,
          source_url: `https://www.legislation.gov.uk/ukpga/2006/46/section/${askCount}`,
        };
        if (askCount === 1) {
          return sseResponse([
            {
              event: "route",
              data: {
                type: "route",
                status: "ROUTE_BOUNDED",
                next_action: "RETRIEVE_BOUNDED",
                coordinates: [win.coordinate],
                corrections: [],
                messages: [],
                index_snapshot: "2026-09-28",
              },
            },
            {
              event: "sources",
              data: { type: "sources", windows: [win] },
            },
            {
              event: "extractive",
              data: { type: "extractive", reason: "quote_requested", windows: [win] },
            },
            {
              event: "receipt",
              data: {
                type: "receipt",
                answer_id: "ans-1",
                fact: {
                  schema: 1,
                  mode: "extractive",
                  corpus_id: "ukpga",
                  policy_hash: "ph",
                  query_sha256: "qh",
                  route_status: "ROUTE_BOUNDED",
                  coordinates: [win.coordinate],
                  passages: { [pid]: win.coordinate },
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
                timings: { first_source_ms: 10, first_sentence_ms: null, total_ms: 20 },
              },
            },
          ]);
        }
        return sseResponse([
          {
            event: "route",
            data: {
              type: "route",
              status: "ROUTE_BOUNDED",
              next_action: "RETRIEVE_BOUNDED",
              coordinates: [win.coordinate],
              corrections: [],
              messages: [],
              index_snapshot: "2026-09-28",
            },
          },
          {
            event: "sources",
            data: { type: "sources", windows: [win] },
          },
          {
            event: "sentence",
            data: {
              type: "sentence",
              sentence: {
                index: 0,
                passage_ids: [pid],
                reasons: ["GROUNDED"],
                rollbacks: 0,
                text: "Generated answer sentence.",
                verdict: "supported",
              },
              citations: [
                {
                  coordinate: win.coordinate,
                  label: "s.2 Companies Act 2006",
                  passage_id: pid,
                  url: win.source_url,
                },
              ],
            },
          },
          {
            event: "receipt",
            data: {
              type: "receipt",
              answer_id: "ans-2",
              fact: {
                schema: 1,
                mode: "generated",
                corpus_id: "ukpga",
                policy_hash: "ph",
                query_sha256: "qh",
                route_status: "ROUTE_BOUNDED",
                coordinates: [win.coordinate],
                passages: { [pid]: win.coordinate },
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
              timings: { first_source_ms: 10, first_sentence_ms: 15, total_ms: 25 },
            },
          },
        ]);
      }
      return new Response("Not found", { status: 404 });
    });

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);

    await user.type(textarea, "Quote s.1 Companies Act 2006{Enter}");
    await waitFor(() => {
      expect(screen.getByText("Statutory provision text 1.")).toBeInTheDocument();
    });

    const mainEl = screen.getByRole("main");
    expect(screen.queryByRole("heading", { name: SOURCES_HEADING })).not.toBeInTheDocument();
    expect(document.querySelector("[data-sources-column]")).toBeNull();
    expect(document.getElementById("w-1-p-1")).toBeNull();
    expect(mainEl.closest(".max-w-\\[728px\\]")).not.toBeNull();
    expect(mainEl.closest(".lg\\:max-w-\\[1120px\\]")).toBeNull();
    expect(mainEl.parentElement).not.toHaveClass("lg:grid-cols-[minmax(0,680px)_360px]");

    await user.type(textarea, "Explain s.2 Companies Act 2006{Enter}");
    await waitFor(() => {
      expect(document.getElementById("w-2-p-2")).toBeInTheDocument();
    });

    // Earlier quote turn still renders no inline sources; active generated turn uses sticky column.
    expect(document.getElementById("w-1-p-1")).toBeNull();
    expect(within(mainEl).queryByRole("heading", { name: SOURCES_HEADING })).not.toBeInTheDocument();
    const sourcesCol = document.querySelector("[data-sources-column]") as HTMLElement | null;
    expect(sourcesCol).not.toBeNull();
    expect(sourcesCol).toContainElement(document.getElementById("w-2-p-2"));
  });

  it("renders the memonsystems link in both states and keeps the hero wordmark non-interactive", async () => {
    const user = userEvent.setup();
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return new Response(
          JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url.endsWith("/api/ask")) {
        return sseResponse([
          {
            event: "done",
            data: { type: "done", timings: { first_source_ms: null, first_sentence_ms: null, total_ms: 10 } },
          },
        ]);
      }
      return new Response("Not found", { status: 404 });
    });

    render(<App />);

    expect(screen.queryByRole("button", { name: START_OVER_LABEL })).not.toBeInTheDocument();
    expect(screen.getByText(WORDMARK).closest("button")).toBeNull();

    const emptyLink = screen.getByRole("link", { name: MEMONSYSTEMS_ARIA_LABEL });
    expect(emptyLink).toHaveTextContent(MEMONSYSTEMS_LABEL);
    expect(emptyLink).toHaveAttribute("href", MEMONSYSTEMS_URL);
    expect(emptyLink).toHaveAttribute("target", "_blank");
    expect(emptyLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(emptyLink.querySelector("svg")).not.toBeNull();
    expect(emptyLink).toHaveClass("text-caption", "text-muted-foreground", "hover:text-foreground", "min-h-11");

    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);
    await user.type(textarea, "First question{Enter}");

    const startOverBtn = await screen.findByRole("button", { name: START_OVER_LABEL });
    expect(startOverBtn).toHaveTextContent(WORDMARK);
    expect(startOverBtn).toHaveClass("hover:text-primary", "no-underline");

    const topBarLink = screen.getByRole("link", { name: MEMONSYSTEMS_ARIA_LABEL });
    expect(topBarLink).toHaveTextContent(MEMONSYSTEMS_LABEL);
    expect(topBarLink).toHaveAttribute("href", MEMONSYSTEMS_URL);
    expect(topBarLink).toHaveAttribute("target", "_blank");
    expect(topBarLink).toHaveAttribute("rel", "noopener noreferrer");
    expect(topBarLink.querySelector("svg")).not.toBeNull();
  });

  it("resets to the empty state, clears and focuses the composer, scrolls to top, and aborts an in-flight stream on Start over", async () => {
    const user = userEvent.setup();
    const scrollToSpy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    let capturedSignal: AbortSignal | undefined;

    globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/health")) {
        return Promise.resolve(
          new Response(JSON.stringify({ status: "ok", corpus_id: "ukpga", snapshot: "2026-09-28" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
        );
      }
      if (url.endsWith("/api/ask")) {
        capturedSignal = init?.signal ?? undefined;
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        });
      }
      return Promise.resolve(new Response("Not found", { status: 404 }));
    });

    render(<App />);
    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);
    await user.type(textarea, "In-flight question{Enter}");

    const startOverBtn = await screen.findByRole("button", { name: START_OVER_LABEL });
    await user.type(textarea, "Draft follow-up");
    expect(textarea).toHaveValue("Draft follow-up");

    await user.click(startOverBtn);

    expect(capturedSignal?.aborted).toBe(true);
    expect(screen.queryByRole("button", { name: START_OVER_LABEL })).not.toBeInTheDocument();
    expect(screen.queryByText("In-flight question")).not.toBeInTheDocument();
    expect(screen.getByText(PURPOSE_LINE)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: EXAMPLES_LABEL })).toBeInTheDocument();
    expect(textarea).toHaveValue("");
    expect(textarea).toHaveFocus();
    expect(scrollToSpy).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
    expect(screen.getByRole("link", { name: MEMONSYSTEMS_ARIA_LABEL })).toHaveAttribute("href", MEMONSYSTEMS_URL);
  });
});

describe("QueryBox", () => {
  it("puts the focus ring only on the composer pill and not on the inner textarea", () => {
    const inputRef = createRef<HTMLTextAreaElement>();
    render(<QueryBox busy={false} blocked={null} onAsk={vi.fn()} inputRef={inputRef} />);

    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);
    const pill = textarea.parentElement;
    expect(pill).toHaveClass("focus-within:outline-2", "focus-within:outline-ring");
    expect(textarea).toHaveClass("outline-none", "focus:outline-none", "focus-visible:outline-none");
  });

  it("disables send when empty or busy, sends on Enter, and inserts newline on Shift+Enter", async () => {
    const user = userEvent.setup();
    const onAsk = vi.fn();
    const inputRef = createRef<HTMLTextAreaElement>();

    const { rerender } = render(
      <QueryBox busy={false} blocked={null} onAsk={onAsk} inputRef={inputRef} />,
    );

    const textarea = screen.getByPlaceholderText(COMPOSER_PLACEHOLDER);
    const sendButton = screen.getByRole("button", { name: SEND_LABEL });

    expect(sendButton).toBeDisabled();

    await user.type(textarea, "Line one");
    expect(sendButton).toBeEnabled();

    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(textarea, "Line two");
    expect(onAsk).not.toHaveBeenCalled();
    expect(textarea).toHaveValue("Line one\nLine two");

    // Disabled while streaming
    rerender(<QueryBox busy={true} blocked={null} onAsk={onAsk} inputRef={inputRef} />);
    expect(sendButton).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(onAsk).not.toHaveBeenCalled();

    // Enabled again when not streaming; Enter sends and clears
    rerender(<QueryBox busy={false} blocked={null} onAsk={onAsk} inputRef={inputRef} />);
    await user.keyboard("{Enter}");
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onAsk).toHaveBeenCalledWith("Line one\nLine two");
    expect(textarea).toHaveValue("");
    expect(sendButton).toBeDisabled();
  });
});

describe("Footer and theme control", () => {
  it("renders the required legal lines and snapshot date", () => {
    render(<Footer snapshot="2026-09-28" />);

    expect(screen.getByText(FOOTER_NOT_ADVICE)).toBeInTheDocument();
    expect(screen.getByText(FOOTER_NO_PERSONAL_DATA)).toBeInTheDocument();
    expect(screen.getByText(FOOTER_OGL)).toBeInTheDocument();
    expect(screen.getByText("Law as at 28 September 2026")).toBeInTheDocument();
  });

  it("stacks each legal line without dots above the theme control below 640 px and joins on one line from 640 px", () => {
    render(<Footer snapshot="2026-09-28" />);

    const footer = screen.getByRole("contentinfo");
    const row = footer.firstElementChild;
    const legal = screen.getByText(FOOTER_NOT_ADVICE).closest("p");
    const dots = Array.from(footer.querySelectorAll("span[aria-hidden='true']"));

    expect(row).toHaveClass("flex-col", "sm:flex-row", "sm:items-center", "sm:justify-between");
    expect(legal).toHaveClass("flex-col", "sm:flex-row", "sm:items-center");
    expect(dots).toHaveLength(3);
    for (const dot of dots) {
      expect(dot).toHaveTextContent("·");
      expect(dot).toHaveClass("hidden", "sm:inline");
    }
  });

  it("switches the dark class on <html> and persists the selection", async () => {
    const user = userEvent.setup();
    render(<Footer snapshot="2026-09-28" />);

    const group = screen.getByRole("group", { name: THEME_LABEL });
    expect(group).toHaveClass("h-8");
    const systemBtn = within(group).getByRole("button", { name: THEME_SYSTEM });
    const lightBtn = within(group).getByRole("button", { name: THEME_LIGHT });
    const darkBtn = within(group).getByRole("button", { name: THEME_DARK });

    for (const btn of [systemBtn, lightBtn, darkBtn]) {
      expect(btn.textContent).toBe("");
      expect(btn.querySelector("svg")).not.toBeNull();
    }

    expect(systemBtn).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    await user.click(darkBtn);
    expect(darkBtn).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    await user.click(lightBtn);
    expect(lightBtn).toHaveAttribute("aria-pressed", "true");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");

    await user.click(systemBtn);
    expect(systemBtn).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });
});
