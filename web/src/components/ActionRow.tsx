import { Check, CircleAlert, Copy, Download, Info } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { copyText } from "../lib/clipboard";
import { cn } from "../lib/utils";
import { panelWindows, shouldShowProvisions, type Turn as TurnData } from "../state/reducer";
import {
  ACTION_COPY,
  ACTION_DETAILS,
  ACTION_DOWNLOAD_EVIDENCE,
  answerIdCopyLine,
  COPIED_TOAST,
  COPY_FAILED_TOAST,
  EVIDENCE_DOWNLOADED_TOAST,
  EVIDENCE_MISMATCH,
  formatDetailsTimings,
  noticeTitle,
  refusalTitle,
  SOURCES_HEADING,
} from "../text";
import { downloadReceipt, ReceiptLine, verifyReceiptHash } from "./Receipt";
import { Button } from "./ui/button";
import { useToast } from "./ui/toast";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./ui/tooltip";

interface Props {
  turn: TurnData;
}

const COPIED_RESET_MS = 1500;

/** Format a finished turn into the plain-text R4 clipboard layout. */
export function formatAnswerCopyText(turn: TurnData): string {
  const sections: string[] = [];

  if (turn.sentences.length > 0) {
    const lines = turn.sentences.map((ev) => {
      if (ev.sentence.verdict !== "refused" && ev.citations.length > 0) {
        const labels = ev.citations.map((c) => c.label).join(", ");
        return `${ev.sentence.text} [${labels}]`;
      }
      return ev.sentence.text;
    });
    sections.push(lines.join("\n"));
  } else if (turn.extractive !== null && turn.extractive.windows.length > 0) {
    sections.push(turn.extractive.windows.map((w) => `${w.heading}\n${w.text}`).join("\n\n"));
  } else if (turn.refusal !== null) {
    sections.push(`${refusalTitle(turn.refusal.kind)}\n${turn.refusal.message}`);
  }

  const sources: { label: string; url: string }[] = [];
  const seen = new Set<string>();
  for (const ev of turn.sentences) {
    if (ev.sentence.verdict === "refused") continue;
    for (const c of ev.citations) {
      const key = `${c.label}\0${c.url}`;
      if (!seen.has(key)) {
        seen.add(key);
        sources.push({ label: c.label, url: c.url });
      }
    }
  }
  if (sources.length === 0) {
    for (const w of panelWindows(turn)) {
      const key = `${w.heading}\0${w.source_url}`;
      if (!seen.has(key)) {
        seen.add(key);
        sources.push({ label: w.heading, url: w.source_url });
      }
    }
  }
  if (sources.length > 0) {
    const sourceLines = sources.map((s) => `- ${s.label}: ${s.url}`);
    sections.push(`${SOURCES_HEADING}:\n${sourceLines.join("\n")}`);
  }

  if (turn.receipt !== null) {
    sections.push(answerIdCopyLine(turn.receipt.answer_id));
  }

  return sections.join("\n\n");
}

/** Whether a turn has a generated or extractive answer eligible for the action row. */
export function hasAnswerContent(turn: TurnData): boolean {
  return (
    turn.sentences.length > 0 ||
    (turn.extractive !== null && turn.extractive.windows.length > 0 && shouldShowProvisions(turn))
  );
}

/** Post-answer action row: Copy, Download evidence, and Details toggle. */
export function ActionRow({ turn }: Props) {
  const [copied, setCopied] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [mismatchAnswerId, setMismatchAnswerId] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const detailsId = useId();
  const { showToast } = useToast();

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
    };
  }, []);

  if (turn.phase !== "done" || !hasAnswerContent(turn)) {
    return null;
  }

  async function handleCopy() {
    const ok = await copyText(formatAnswerCopyText(turn));
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

  async function handleDownloadEvidence() {
    if (!turn.receipt) return;
    const targetReceipt = turn.receipt;
    downloadReceipt(targetReceipt);
    const result = await verifyReceiptHash(targetReceipt);
    if (result === "mismatch") {
      setMismatchAnswerId(targetReceipt.answer_id);
    } else {
      setMismatchAnswerId(null);
      showToast(EVIDENCE_DOWNLOADED_TOAST);
    }
  }

  const hasEvidenceMismatch = turn.receipt !== null && mismatchAnswerId === turn.receipt.answer_id;
  const hasFallbackNotice = turn.notices.some((n) => n.code === "provider_fallback");
  const fallbackTitle = noticeTitle("provider_fallback") ?? "Backup model used";
  const hasDetails = turn.receipt !== null || turn.timings !== null || hasFallbackNotice;

  return (
    <TooltipProvider delayDuration={300}>
      <div className="mt-4 flex flex-wrap items-center gap-1">
        {hasAnswerContent(turn) && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="md"
                className="h-11 w-11 px-0 text-muted-foreground hover:text-primary min-[400px]:w-auto min-[400px]:px-3"
                aria-label={ACTION_COPY}
                onClick={() => void handleCopy()}
              >
                {copied ? (
                  <Check className="h-4 w-4 text-primary copy-morph" aria-hidden="true" />
                ) : (
                  <Copy className="h-4 w-4" aria-hidden="true" />
                )}
                <span className="hidden min-[400px]:inline">{ACTION_COPY}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{ACTION_COPY}</TooltipContent>
          </Tooltip>
        )}

        {turn.receipt !== null && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="md"
                className="h-11 w-11 px-0 text-muted-foreground hover:text-primary min-[400px]:w-auto min-[400px]:px-3"
                aria-label={ACTION_DOWNLOAD_EVIDENCE}
                onClick={() => void handleDownloadEvidence()}
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                <span className="hidden min-[400px]:inline">{ACTION_DOWNLOAD_EVIDENCE}</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{ACTION_DOWNLOAD_EVIDENCE}</TooltipContent>
          </Tooltip>
        )}

        {hasDetails && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className={cn(
                  "text-muted-foreground hover:text-primary",
                  detailsOpen && "bg-primary-soft text-primary",
                )}
                aria-label={ACTION_DETAILS}
                aria-expanded={detailsOpen}
                aria-controls={detailsId}
                onClick={() => setDetailsOpen((open) => !open)}
              >
                <Info className="h-4 w-4" aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>{ACTION_DETAILS}</TooltipContent>
          </Tooltip>
        )}

        {hasEvidenceMismatch && (
          <span
            aria-live="polite"
            className="ml-1 inline-flex items-center gap-1.5 text-caption text-muted-foreground"
          >
            <CircleAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{EVIDENCE_MISMATCH}</span>
          </span>
        )}
      </div>

      {hasDetails && detailsOpen && (
        <div id={detailsId} className="mt-2 space-y-0.5 text-caption text-muted-foreground">
          {turn.receipt && <ReceiptLine receipt={turn.receipt} />}
          {turn.timings && (
            <p className="tabular-nums">
              {formatDetailsTimings(turn.timings.first_sentence_ms, turn.timings.total_ms)}
            </p>
          )}
          {hasFallbackNotice && <p>{fallbackTitle}</p>}
        </div>
      )}
    </TooltipProvider>
  );
}
