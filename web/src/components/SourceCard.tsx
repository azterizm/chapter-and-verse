import { Check, ChevronDown, Copy, ExternalLink } from "lucide-react";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { useToast } from "@/components/ui/toast";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { Window } from "../api/events";
import { windowAnchor } from "../state/reducer";
import {
  ACTION_COPY,
  COPIED_TOAST,
  COPY_FAILED_TOAST,
  formatDate,
  formatSourceCopy,
  parseSourceTitle,
  SOURCE_LINK_LABEL,
  SOURCE_LINK_NEW_TAB,
  sourceVersionDate,
} from "../text";
import { StatusBadge } from "./StatusBadge";
import { WindowText } from "./WindowText";

const COPY_FEEDBACK_MS = 1500;

const reducedMotion = () =>
  typeof window !== "undefined" && (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);

export interface SourceCardProps {
  window: Window;
  turnId: number;
  citationLabel?: string | undefined;
  open?: boolean | undefined;
  defaultOpen?: boolean | undefined;
  onOpenChange?: ((open: boolean) => void) | undefined;
}

export function SourceCard({
  window: w,
  turnId,
  citationLabel,
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
}: SourceCardProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(defaultOpen);
  const isOpen = controlledOpen ?? uncontrolledOpen;

  const [pulsing, setPulsing] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const { showToast } = useToast();

  const articleRef = React.useRef<HTMLElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const copyTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const pulseTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const anchor = windowAnchor(turnId, w.passage_id);
  const headingId = `${anchor}-h`;
  const statusId = `${anchor}-status`;
  const contentId = `${anchor}-content`;
  const hasStatusBadge = w.status !== "IN_FORCE";
  const isRepealed = w.status === "REPEALED";
  const title = parseSourceTitle(w.heading, w.coordinate, citationLabel);

  const setOpen = React.useCallback(
    (next: boolean) => {
      if (controlledOpen === undefined) {
        setUncontrolledOpen(next);
      }
      onOpenChange?.(next);
    },
    [controlledOpen, onOpenChange],
  );

  React.useEffect(() => {
    return () => {
      if (copyTimerRef.current !== null) clearTimeout(copyTimerRef.current);
      if (pulseTimerRef.current !== null) clearTimeout(pulseTimerRef.current);
    };
  }, []);

  const triggerPulse = React.useCallback(() => {
    if (pulseTimerRef.current !== null) clearTimeout(pulseTimerRef.current);
    const scheduleClear = () => {
      pulseTimerRef.current = setTimeout(() => {
        pulseTimerRef.current = null;
        setPulsing(false);
      }, 950);
    };
    if (pulsing) {
      setPulsing(false);
      requestAnimationFrame(() => {
        setPulsing(true);
        scheduleClear();
      });
    } else {
      setPulsing(true);
      scheduleClear();
    }
  }, [pulsing]);

  const handleArticleFocus = (e: React.FocusEvent<HTMLElement>) => {
    // Only programmatic jump-to-source focuses the <article> root itself (tabIndex=-1).
    if (e.target !== e.currentTarget) return;
    if (!isOpen) setOpen(true);
    triggerPulse();
    requestAnimationFrame(() => {
      const el = articleRef.current;
      if (el && typeof el.scrollIntoView === "function") {
        el.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
      }
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape" && isOpen) {
      e.stopPropagation();
      setOpen(false);
      setPulsing(false);
      triggerRef.current?.focus({ preventScroll: true });
    }
  };

  const handleCopy = async () => {
    const label = citationLabel ?? w.heading;
    const ok = await copyText(formatSourceCopy(w.text, label, w.source_url));
    if (ok) {
      setCopied(true);
      if (copyTimerRef.current !== null) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => {
        copyTimerRef.current = null;
        setCopied(false);
      }, COPY_FEEDBACK_MS);
      showToast(COPIED_TOAST);
    } else {
      showToast(COPY_FAILED_TOAST);
    }
  };

  return (
    <article
      ref={articleRef}
      id={anchor}
      tabIndex={-1}
      aria-labelledby={headingId}
      onFocus={handleArticleFocus}
      onKeyDown={handleKeyDown}
      className={cn(
        "scroll-mt-6 rounded-card border border-border bg-surface transition-shadow duration-[var(--dur-base)] ease-[var(--ease-out)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        isOpen && "shadow-card",
      )}
    >
      <Collapsible open={isOpen} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <button
            ref={triggerRef}
            type="button"
            aria-controls={contentId}
            aria-labelledby={hasStatusBadge ? `${headingId} ${statusId}` : headingId}
            className="w-full min-h-11 cursor-pointer rounded-card px-4 py-3 text-left transition-colors duration-[var(--dur-fast)] ease-[var(--ease-out)] hover:bg-primary-soft/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <span className="flex items-start justify-between gap-2">
              <span id={headingId} role="heading" aria-level={3} className="min-w-0 flex-1">
                <span className="flex min-w-0 items-baseline gap-1.5">
                  <span
                    className={`shrink-0 text-ui font-bold ${isRepealed ? "text-muted-foreground line-through" : "text-foreground"}`}
                  >
                    {title.provision}
                  </span>
                  {title.provisionHeading && (
                    <span className="min-w-0 truncate text-ui text-muted-foreground">
                      {title.provisionHeading}
                    </span>
                  )}
                </span>
                {title.instrument && (
                  <span className="mt-0.5 block text-caption text-muted-foreground">
                    {title.instrument}
                  </span>
                )}
              </span>
              <span className="flex shrink-0 items-center gap-2 pt-0.5">
                <StatusBadge id={statusId} status={w.status} />
                <ChevronDown
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground transition-transform duration-[var(--dur-base)] ease-[var(--ease-out)]",
                    isOpen && "rotate-180",
                  )}
                  aria-hidden="true"
                />
              </span>
            </span>
            {!isOpen && (
              <span className="mt-1.5 line-clamp-2 block text-caption text-muted-foreground">{w.text}</span>
            )}
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent id={contentId}>
          <div className="space-y-3 px-4 pt-1 pb-4" aria-hidden={!isOpen}>
            <WindowText
              text={w.text}
              highlight={w.highlight}
              pulsing={pulsing}
              onPulseEnd={() => setPulsing(false)}
            />

            {w.version_date && (
              <p className="text-caption text-muted-foreground">{sourceVersionDate(formatDate(w.version_date))}</p>
            )}

            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Button type="button" variant="ghost" size="md" onClick={handleCopy}>
                {copied ? (
                  <Check className="copy-morph size-4 text-primary" aria-hidden="true" />
                ) : (
                  <Copy className="copy-morph size-4" aria-hidden="true" />
                )}
                {ACTION_COPY}
              </Button>

              <Button variant="ghost" size="md" asChild>
                <a href={w.source_url} target="_blank" rel="noopener noreferrer">
                  {SOURCE_LINK_LABEL}
                  <span className="sr-only">{SOURCE_LINK_NEW_TAB}</span>
                  <ExternalLink className="size-4" aria-hidden="true" />
                </a>
              </Button>
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </article>
  );
}
