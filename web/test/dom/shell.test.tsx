// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
  PURPOSE_LINE,
  SEND_LABEL,
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

    // Flex column shell: min-h-dvh, main grows, composer docked at bottom inside main, footer after.
    const mainEl = screen.getByRole("main");
    const shell = mainEl.closest(".min-h-dvh");
    expect(shell).toHaveClass("flex", "flex-col");
    expect(mainEl).toHaveClass("flex", "w-full", "flex-1", "flex-col");

    const dock = mainEl.querySelector("[data-composer-dock]");
    expect(dock).not.toBeNull();
    expect(dock).toHaveClass("sticky", "bottom-0", "mt-auto");
    expect(mainEl.closest(".max-w-\\[728px\\]")).not.toBeNull();
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
