// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ToastProvider, useToast } from "@/components/ui/toast";
import { copyText } from "@/lib/clipboard";
import {
  THEME_STORAGE_KEY,
  getResolvedTheme,
  getTheme,
  initTheme,
  setTheme,
} from "@/lib/theme";
import { cn } from "@/lib/utils";

function createMatchMediaStub(initialMatches: boolean) {
  let matches = initialMatches;
  const listeners = new Set<(event: MediaQueryListEvent) => void>();

  const stub = vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.add(listener);
    },
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.delete(listener);
    },
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));

  return {
    stub,
    setMatches(next: boolean) {
      matches = next;
      const event = { matches: next, media: "(prefers-color-scheme: dark)" } as MediaQueryListEvent;
      for (const listener of listeners) {
        listener(event);
      }
    },
  };
}

function parseCssBlockVars(css: string, selectorPattern: RegExp): Record<string, string> {
  const match = selectorPattern.exec(css);
  if (!match?.[1]) {
    throw new Error(`Could not find CSS block matching ${selectorPattern}`);
  }
  const vars: Record<string, string> = {};
  for (const line of match[1].split("\n")) {
    const varMatch = /^\s*(--[a-z-]+):\s*(#[0-9a-fA-F]{6})\s*;/.exec(line);
    if (varMatch?.[1] && varMatch[2]) {
      vars[varMatch[1]] = varMatch[2];
    }
  }
  return vars;
}

function relativeLuminance(hex: string): number {
  const clean = hex.replace("#", "");
  const r = parseInt(clean.slice(0, 2), 16) / 255;
  const g = parseInt(clean.slice(2, 4), 16) / 255;
  const b = parseInt(clean.slice(4, 6), 16) / 255;
  const linearize = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b);
}

function contrastRatio(fgHex: string, bgHex: string): number {
  const l1 = relativeLuminance(fgHex);
  const l2 = relativeLuminance(bgHex);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
  document.documentElement.className = "";
  document.documentElement.removeAttribute("data-theme");
  try {
    window.localStorage.clear();
  } catch {
    // Ignore when localStorage is mocked to throw.
  }
});

describe("cn utility", () => {
  it("merges conditional classes and resolves Tailwind conflicts", () => {
    const inactive = false as boolean;
    expect(cn("px-2 py-1", inactive && "hidden", "px-4")).toBe("py-1 px-4");
  });
});

describe("theme module", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("defaults to system and follows prefers-color-scheme changes", () => {
    const mq = createMatchMediaStub(false);
    vi.stubGlobal("matchMedia", mq.stub);

    expect(initTheme()).toBe("system");
    expect(getTheme()).toBe("system");
    expect(getResolvedTheme()).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    mq.setMatches(true);
    expect(getResolvedTheme()).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("persists explicit light/dark choices and survives reload", () => {
    const mq = createMatchMediaStub(false);
    vi.stubGlobal("matchMedia", mq.stub);
    initTheme();

    setTheme("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    expect(initTheme()).toBe("dark");
    expect(getResolvedTheme()).toBe("dark");

    setTheme("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    mq.setMatches(true);
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    setTheme("system");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("survives a throwing localStorage", () => {
    const mq = createMatchMediaStub(false);
    vi.stubGlobal("matchMedia", mq.stub);

    const throwingStorage = {
      getItem: vi.fn(() => {
        throw new Error("storage blocked");
      }),
      setItem: vi.fn(() => {
        throw new Error("storage blocked");
      }),
      removeItem: vi.fn(() => {
        throw new Error("storage blocked");
      }),
      clear: vi.fn(),
      key: vi.fn(() => null),
      length: 0,
    };
    vi.spyOn(window, "localStorage", "get").mockReturnValue(throwingStorage);

    expect(() => initTheme()).not.toThrow();
    expect(getTheme()).toBe("system");

    expect(() => setTheme("dark")).not.toThrow();
    expect(getTheme()).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});

describe("clipboard helper", () => {
  it("uses navigator.clipboard.writeText when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const execCommand = vi.fn(() => true);
    Object.defineProperty(document, "execCommand", {
      value: execCommand,
      configurable: true,
    });

    await expect(copyText("s.2 Limitation Act 1980")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("s.2 Limitation Act 1980");
    expect(execCommand).not.toHaveBeenCalled();
  });

  it("falls back to hidden textarea and execCommand when Clipboard API fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("NotAllowedError"));
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const execCommand = vi.fn(() => {
      const ta = document.body.querySelector("textarea");
      expect(ta).not.toBeNull();
      expect(ta?.value).toBe("Fallback text");
      return true;
    });
    Object.defineProperty(document, "execCommand", {
      value: execCommand,
      configurable: true,
    });

    await expect(copyText("Fallback text")).resolves.toBe(true);
    expect(execCommand).toHaveBeenCalledWith("copy");
    expect(document.body.querySelector("textarea")).toBeNull();
  });

  it("returns false when both Clipboard API and execCommand fail", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: undefined,
      configurable: true,
    });
    Object.defineProperty(document, "execCommand", {
      value: vi.fn(() => false),
      configurable: true,
    });

    await expect(copyText("Nothing works")).resolves.toBe(false);
    expect(document.body.querySelector("textarea")).toBeNull();
  });
});

describe("ui primitives", () => {
  it("renders Button with accessible name and supports variants and clicks", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();

    render(
      <div>
        <Button variant="primary" size="icon" aria-label="Send" onClick={onClick} />
        <Button variant="ghost" size="sm">
          Copy
        </Button>
        <Button variant="outline" size="md" disabled>
          Disabled
        </Button>
      </div>,
    );

    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toHaveAttribute("type", "button");
    await user.click(send);
    expect(onClick).toHaveBeenCalledTimes(1);

    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disabled" })).toBeDisabled();
  });

  it("renders Chip with accessible name and selected state", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();

    const { rerender } = render(
      <Chip selected={false} onClick={onSelect}>
        s.2 Limitation Act 1980
      </Chip>,
    );

    const chip = screen.getByRole("button", { name: "s.2 Limitation Act 1980" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    expect(chip).not.toHaveAttribute("data-selected");

    await user.click(chip);
    expect(onSelect).toHaveBeenCalledTimes(1);

    rerender(
      <Chip selected={true} onClick={onSelect}>
        s.2 Limitation Act 1980
      </Chip>,
    );
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(chip).toHaveAttribute("data-selected", "true");
  });

  it("keeps CollapsibleContent mounted while closed and toggles open state", async () => {
    const user = userEvent.setup();
    render(
      <Collapsible>
        <CollapsibleTrigger>Toggle source</CollapsibleTrigger>
        <CollapsibleContent>
          <p id="mounted-clause">Full statutory text stays mounted.</p>
        </CollapsibleContent>
      </Collapsible>,
    );

    const clause = screen.getByText("Full statutory text stays mounted.");
    const contentEl = clause.closest("[data-state]");
    expect(contentEl).toHaveAttribute("data-state", "closed");

    await user.click(screen.getByRole("button", { name: "Toggle source" }));
    expect(contentEl).toHaveAttribute("data-state", "open");
  });

  it("shows one toast at a time and dismisses after 1.6 s", () => {
    vi.useFakeTimers();

    function Trigger() {
      const { showToast } = useToast();
      return (
        <div>
          <button type="button" onClick={() => showToast("Copied")}>
            First
          </button>
          <button type="button" onClick={() => showToast("Copy failed")}>
            Second
          </button>
        </div>
      );
    }

    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );

    act(() => {
      screen.getByRole("button", { name: "First" }).click();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Copied");

    act(() => {
      screen.getByRole("button", { name: "Second" }).click();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Copy failed");

    act(() => {
      vi.advanceTimersByTime(1600);
    });
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });
});

describe("design token contrast (WCAG AA)", () => {
  const localCss = resolve(process.cwd(), "src/index.css");
  const indexCssPath = existsSync(localCss) ? localCss : resolve(process.cwd(), "web/src/index.css");
  const css = readFileSync(indexCssPath, "utf8");
  const light = parseCssBlockVars(css, /:root\s*\{([\s\S]*?)\n\}/);
  const dark = parseCssBlockVars(css, /\n\.dark\s*\{([\s\S]*?)\n\}/);

  it.each([
    ["light", light],
    ["dark", dark],
  ] as const)("%s theme meets 4.5:1 text and 3:1 UI contrast", (_name, tokens) => {
    const bg = tokens["--background"];
    const surface = tokens["--surface"];
    const fg = tokens["--foreground"];
    const muted = tokens["--muted-foreground"];
    const primary = tokens["--primary"];
    const primaryFg = tokens["--primary-foreground"];
    const primarySoft = tokens["--primary-soft"];
    const ring = tokens["--ring"];

    expect(bg).toBeDefined();
    expect(surface).toBeDefined();
    expect(fg).toBeDefined();
    expect(muted).toBeDefined();
    expect(primary).toBeDefined();
    expect(primaryFg).toBeDefined();
    expect(primarySoft).toBeDefined();
    expect(ring).toBeDefined();

    if (!bg || !surface || !fg || !muted || !primary || !primaryFg || !primarySoft || !ring) {
      throw new Error("Missing required theme tokens");
    }

    expect(contrastRatio(fg, bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(fg, surface)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(muted, bg)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(muted, surface)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(primaryFg, primary)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(primary, primarySoft)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(primary, bg)).toBeGreaterThanOrEqual(3.0);
    expect(contrastRatio(primary, surface)).toBeGreaterThanOrEqual(3.0);
    expect(contrastRatio(ring, bg)).toBeGreaterThanOrEqual(3.0);
  });

  it("removes styles.css from main.tsx and uses Satoshi and print rules in index.css", () => {
    const localMain = resolve(process.cwd(), "src/main.tsx");
    const mainPath = existsSync(localMain) ? localMain : resolve(process.cwd(), "web/src/main.tsx");
    const mainSource = readFileSync(mainPath, "utf8");

    expect(mainSource).not.toContain("styles.css");
    expect(css).toContain('"Satoshi"');
    expect(css).toContain("font-family: var(--font-sans)");
    expect(css).toContain("@media print");
  });
});
