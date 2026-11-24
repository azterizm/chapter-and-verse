import { Check, Copy, ExternalLink } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Window } from "../api/events";
import { copyText } from "../lib/clipboard";
import { windowAnchor } from "../state/reducer";
import { COPIED_TOAST, COPY_FAILED_TOAST, copyQuoteLabel, quoteLinkLabel } from "../text";
import { Button } from "./ui/button";
import { useToast } from "./ui/toast";
import { WindowText } from "./WindowText";

interface Props {
  window: Window;
  turnId: number;
  allowCopy: boolean;
  label?: string | undefined;
  grouped?: boolean | undefined;
  onShowSource?: ((turnId: number, passageId: string) => void) | undefined;
}

const COPIED_RESET_MS = 1500;

function textStartsWithLabel(text: string, label: string): boolean {
  const trimmedText = text.trimStart();
  const trimmedLabel = label.trim();
  if (!trimmedLabel || !trimmedText.startsWith(trimmedLabel)) return false;
  if (trimmedLabel.endsWith(")")) return true;
  const next = trimmedText.slice(trimmedLabel.length, trimmedLabel.length + 1);
  return next === "" || /[\s.()[\]:;,\-\u2013\u2014]/.test(next);
}

/** Quotation block for extractive answers with left primary rule, optional copy button, and source link. */
export function QuoteBlock({ window: w, turnId, allowCopy, label, grouped = false, onShowSource }: Props) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { showToast } = useToast();
  const displayLabel = label ?? w.heading;
  const hideLabel = Boolean(grouped && label && !onShowSource && textStartsWithLabel(w.text, label));

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  async function handleCopy() {
    const payload = `${w.heading}\n${w.text}\n${w.source_url}`;
    const ok = await copyText(payload);
    if (ok) {
      setCopied(true);
      showToast(COPIED_TOAST);
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        setCopied(false);
      }, COPIED_RESET_MS);
    } else {
      showToast(COPY_FAILED_TOAST);
    }
  }

  const actions = (
    <div className="-mr-2 -mt-2 flex shrink-0 items-center gap-0.5">
      {allowCopy && (
        <Button
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-primary"
          aria-label={copyQuoteLabel(displayLabel)}
          onClick={() => void handleCopy()}
        >
          {copied ? (
            <Check className="h-4 w-4 text-primary copy-morph" aria-hidden="true" />
          ) : (
            <Copy className="h-4 w-4" aria-hidden="true" />
          )}
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-primary"
        asChild
      >
        <a
          href={w.source_url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={quoteLinkLabel(displayLabel)}
        >
          <ExternalLink className="h-4 w-4" aria-hidden="true" />
        </a>
      </Button>
    </div>
  );

  return (
    <blockquote className="border-l-[3px] border-primary pl-4 py-1" cite={w.source_url}>
      {hideLabel ? (
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <WindowText text={w.text} highlight={w.highlight} />
          </div>
          {actions}
        </div>
      ) : (
        <>
          <div className="flex items-start justify-between gap-2">
            <p className="text-ui font-bold text-foreground tabular-nums">
              {onShowSource ? (
                <a
                  href={`#${windowAnchor(turnId, w.passage_id)}`}
                  onClick={(e) => {
                    e.preventDefault();
                    onShowSource(turnId, w.passage_id);
                  }}
                  className="hover:text-primary hover:underline underline-offset-4"
                >
                  {displayLabel}
                </a>
              ) : (
                displayLabel
              )}
            </p>
            {actions}
          </div>
          <div className="mt-1.5 text-body text-foreground">
            <WindowText text={w.text} highlight={w.highlight} />
          </div>
        </>
      )}
    </blockquote>
  );
}
