// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Window } from "@/api/events";
import { SourceCard } from "@/components/SourceCard";
import { SourcePanel } from "@/components/SourcePanel";
import { StatusBadge } from "@/components/StatusBadge";
import { WindowText } from "@/components/WindowText";
import { ToastProvider } from "@/components/ui/toast";
import { type Turn, windowAnchor } from "@/state/reducer";
import { COPIED_TOAST, SOURCE_LINK_LABEL, formatSourceCopy } from "@/text";

function makeWindow(overrides: Partial<Window> = {}): Window {
  return {
    passage_id: "P1",
    coordinate: "uk/ukpga/1996/18/s124/1",
    heading: "Employment Rights Act 1996, section 124(1)",
    text: "(1) The amount of any compensation awarded to a person under section 117 shall not exceed the amount specified in subsection (1ZA).",
    status: "IN_FORCE",
    version_date: "2026-07-29",
    source_url: "https://www.legislation.gov.uk/ukpga/1996/18/section/124/1",
    highlight: [75, 134],
    ...overrides,
  };
}

function makeTurn(overrides: Partial<Turn> = {}): Turn {
  return {
    id: 1,
    query: "What is the limit of the compensatory award?",
    phase: "done",
    route: null,
    clarify: null,
    refusal: null,
    windows: [
      makeWindow(),
      makeWindow({
        passage_id: "P2",
        coordinate: "uk/ukpga/1996/18/s124/1ZA",
        heading: "Employment Rights Act 1996, section 124(1ZA)",
        text: "(1ZA) The amount specified in this subsection is the lower of £123,543 and 52 multiplied by a week’s pay.",
        highlight: [49, 102],
        source_url: "https://www.legislation.gov.uk/ukpga/1996/18/section/124/1ZA",
      }),
    ],
    sentences: [
      {
        type: "sentence",
        sentence: {
          index: 0,
          text: "The award cannot exceed the limit in subsection (1ZA).",
          verdict: "supported",
          passage_ids: ["P1"],
          reasons: ["GROUNDED"],
          rollbacks: 0,
        },
        citations: [
          {
            passage_id: "P1",
            coordinate: "uk/ukpga/1996/18/s124/1",
            label: "s.124(1) Employment Rights Act 1996",
            url: "https://www.legislation.gov.uk/ukpga/1996/18/section/124/1",
          },
        ],
      },
    ],
    extractive: null,
    notices: [],
    receipt: null,
    error: null,
    timings: null,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("StatusBadge", () => {
  it("renders nothing for IN_FORCE and monochrome pills for other statuses", () => {
    const { container, rerender } = render(<StatusBadge status="IN_FORCE" />);
    expect(container).toBeEmptyDOMElement();

    rerender(<StatusBadge status="NOT_YET_IN_FORCE" />);
    expect(screen.getByText("Not in force")).toBeInTheDocument();

    rerender(<StatusBadge status="REPEALED" />);
    expect(screen.getByText("Repealed")).toBeInTheDocument();

    rerender(<StatusBadge status="TEXT_NOT_HELD" />);
    expect(screen.getByText("Text not held")).toBeInTheDocument();

    rerender(<StatusBadge status="HISTORY_NOT_HELD" />);
    expect(screen.getByText("Text not held")).toBeInTheDocument();
  });

  it("strikes through the provision label when a source is repealed", () => {
    const win = makeWindow({ status: "REPEALED" });
    render(<SourceCard window={win} turnId={1} />);
    const label = screen.getByText("s.124(1)");
    expect(label).toHaveClass("line-through", "text-muted-foreground");
    expect(screen.getByText("Repealed")).toBeInTheDocument();
  });
});

describe("WindowText", () => {
  it("renders the highlighted span with primary styling and optional pulse", () => {
    const { rerender } = render(
      <WindowText text="Alpha beta gamma" highlight={[6, 10]} pulsing={false} />,
    );
    const mark = screen.getByText("beta");
    expect(mark.tagName).toBe("MARK");
    expect(mark).toHaveClass("bg-primary-soft", "text-foreground", "underline", "decoration-primary", "decoration-2");
    expect(mark).not.toHaveClass("jump-to-source");

    rerender(<WindowText text="Alpha beta gamma" highlight={[6, 10]} pulsing={true} />);
    expect(screen.getByText("beta")).toHaveClass("jump-to-source");
  });
});

describe("SourcePanel and SourceCard", () => {
  it("shows provision label untruncated, truncated heading if present, and instrument name on line 2", () => {
    const withHeading = makeWindow({
      heading: "Employment Rights Act 1996, section 124(1) (Limit of compensatory award etc)",
      coordinate: "uk/ukpga/1996/18/s124/1",
    });
    const { rerender } = render(<SourceCard window={withHeading} turnId={1} />);

    const prov = screen.getByText("s.124(1)");
    expect(prov).toHaveClass("shrink-0", "text-ui", "font-bold");
    expect(prov).not.toHaveClass("truncate");

    const subheading = screen.getByText("Limit of compensatory award etc");
    expect(subheading).toHaveClass("truncate", "text-ui", "text-muted-foreground");

    const instrument = screen.getByText("Employment Rights Act 1996");
    expect(instrument).toHaveClass("text-caption", "text-muted-foreground");

    const withoutHeading = makeWindow({
      heading: "Limitation Act 1980, section 2",
      coordinate: "uk/ukpga/1980/58/s2",
    });
    rerender(<SourceCard window={withoutHeading} turnId={1} />);
    expect(screen.getByText("s.2")).toHaveClass("shrink-0", "font-bold");
    expect(screen.getByText("Limitation Act 1980")).toHaveClass("text-caption", "text-muted-foreground");
  });

  it("keeps collapsed cards mounted in the DOM with aria-expanded and aria-controls", () => {
    const turn = makeTurn();
    render(<SourcePanel turn={turn} />);

    const p1Card = document.getElementById(windowAnchor(1, "P1"));
    const p2Card = document.getElementById(windowAnchor(1, "P2"));
    expect(p1Card).not.toBeNull();
    expect(p2Card).not.toBeNull();

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);

    for (const btn of buttons) {
      expect(btn).toHaveAttribute("aria-expanded", "false");
      const controlsId = btn.getAttribute("aria-controls");
      expect(controlsId).toBeTruthy();
      const content = controlsId ? document.getElementById(controlsId) : null;
      expect(content).toBeInTheDocument();
      expect(content).toHaveAttribute("data-state", "closed");
    }

    // Full text and legislation link remain mounted in the DOM even while collapsed.
    expect(
      within(p1Card as HTMLElement).getByRole("link", { name: /legislation\.gov\.uk/i, hidden: true }),
    ).toBeInTheDocument();
    expect(p1Card?.querySelector("mark")).not.toBeNull();
  });

  it("toggles with Enter and Space and collapses on Escape", async () => {
    const user = userEvent.setup();
    const turn = makeTurn();
    render(<SourcePanel turn={turn} />);

    const p1Card = document.getElementById(windowAnchor(1, "P1")) as HTMLElement;
    const trigger = within(p1Card).getByRole("button", { name: /s\.124\(1\)/i });
    const controlsId = trigger.getAttribute("aria-controls") ?? "";
    const content = document.getElementById(controlsId);

    trigger.focus();
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(content).toHaveAttribute("data-state", "open");

    await user.keyboard(" ");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(content).toHaveAttribute("data-state", "closed");

    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(content).toHaveAttribute("data-state", "closed");
  });

  it("expands, scrolls smoothly, focuses, and pulses the highlight on jump-to-source", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      value: scrollIntoView,
      configurable: true,
    });
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation(() => ({ matches: false })),
    );

    const turn = makeTurn();
    render(<SourcePanel turn={turn} />);

    const p1Card = document.getElementById(windowAnchor(1, "P1")) as HTMLElement;
    const p2Card = document.getElementById(windowAnchor(1, "P2")) as HTMLElement;

    // Simulate App.tsx's showSource jump path.
    act(() => {
      p2Card.scrollIntoView({ block: "nearest", behavior: "smooth" });
      p2Card.focus({ preventScroll: true });
    });

    expect(document.activeElement).toBe(p2Card);
    const p2Trigger = within(p2Card).getByRole("button", { name: /124\(1ZA\)/i });
    const p1Trigger = within(p1Card).getByRole("button", { name: /124\(1\)/i });
    expect(p2Trigger).toHaveAttribute("aria-expanded", "true");
    expect(p1Trigger).toHaveAttribute("aria-expanded", "false");

    const p2Mark = p2Card.querySelector("mark");
    expect(p2Mark).not.toBeNull();
    expect(p2Mark).toHaveClass("jump-to-source");

    await waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "nearest", behavior: "smooth" });
    });

    // Pressing Escape collapses the jumped-to card.
    const user = userEvent.setup();
    await user.keyboard("{Escape}");
    expect(p2Trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("copies provision text, citation label, and URL, and shows version date and link", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    const turn = makeTurn();
    render(
      <ToastProvider>
        <SourcePanel turn={turn} />
      </ToastProvider>,
    );

    const p1Card = document.getElementById(windowAnchor(1, "P1")) as HTMLElement;
    const trigger = within(p1Card).getByRole("button", { name: /124\(1\)/i });
    await user.click(trigger);

    expect(within(p1Card).getByText("Version of 29 July 2026")).toBeInTheDocument();
    const extLink = within(p1Card).getByRole("link", { name: new RegExp(SOURCE_LINK_LABEL, "i") });
    expect(extLink).toHaveAttribute("href", turn.windows[0]?.source_url);
    expect(extLink).toHaveAttribute("target", "_blank");
    expect(extLink).toHaveAttribute("rel", "noopener noreferrer");

    const copyBtn = within(p1Card).getByRole("button", { name: "Copy" });
    await user.click(copyBtn);

    const w0 = turn.windows[0];
    if (!w0) throw new Error("Missing fixture window");
    expect(writeText).toHaveBeenCalledWith(
      formatSourceCopy(w0.text, "s.124(1) Employment Rights Act 1996", w0.source_url),
    );
    expect(screen.getByRole("status")).toHaveTextContent(COPIED_TOAST);
  });

  it("falls back to window heading in copy format when no sentence citation matches", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    const win = makeWindow({ passage_id: "P9", heading: "Companies Act 2006, section 172(1)" });
    render(<SourceCard window={win} turnId={2} defaultOpen />);

    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith(formatSourceCopy(win.text, win.heading, win.source_url));
  });
});
