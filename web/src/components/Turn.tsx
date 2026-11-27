import { useEffect, useId, useRef, useState } from "react";
import { health } from "../api/client";
import type { LabelledCoordinate, SentenceEvent } from "../api/events";
import { cn } from "../lib/utils";
import { isFallbackExtractive, type Turn as TurnData, type TurnChoice } from "../state/reducer";
import {
  ACTION_CHANGE,
  ACTION_HIDE_PROVISIONS,
  ACTION_RETRY,
  ACTION_SHOW_PROVISIONS,
  ACTION_SHOW_PROVISIONS_WHILE_WAITING,
  candidateCitation,
  citationInstrumentKey,
  CLARIFY_ARIA_LABEL,
  CLARIFY_SHOW_LESS,
  CLARIFY_SHOW_MORE,
  CLARIFY_TITLE,
  composeChoiceQuery,
  EMPTY_ANSWER_TEXT,
  ERROR_CLIENT_TITLE,
  ERROR_SERVER_TITLE,
  EXTRACTIVE_ARIA_LABEL,
  EXTRACTIVE_REASON,
  groupQuoteLabels,
  NOTICES_ARIA_LABEL,
  QUESTION_LABEL,
  STOPPED_TEXT,
  usingCitationText,
  WARMING_BODY,
  WARMING_CANCEL_LABEL,
  WARMING_TIMEOUT_BODY,
  WARMING_TITLE,
} from "../text";
import { ActionRow, hasAnswerContent } from "./ActionRow";
import { Citation } from "./Citation";
import { Notice } from "./Notice";
import { QuoteBlock } from "./QuoteBlock";
import { RefusalCard } from "./RefusalCard";
import { StatusLine } from "./StatusLine";
import { Button } from "./ui/button";
import { Chip } from "./ui/chip";

export interface ReplaceRequest {
  oldTurnId: number;
  query: string;
  question: string;
  choice: TurnChoice | null;
  autoRetry?: boolean;
}

interface Props {
  turn: TurnData;
  busy: boolean;
  onReplace(req: ReplaceRequest): void;
  onShowSource(turnId: number, passageId: string): void;
  onRevealProvisions?(turnId: number): void;
}

const INITIAL_CLARIFY_COUNT = 3;
const INITIAL_CHANGE_COUNT = 5;
const WARMING_POLL_INTERVAL_MS = 10_000;
const WARMING_TIMEOUT_MS = 600_000;
const DEFAULT_WARMING_ESTIMATE_S = 406;
const MAX_WARMING_AUTO_RETRIES = 2;

const PROGRESS_WIDTH_CLASSES: readonly string[] = [
  "w-[5%]",
  "w-[10%]",
  "w-[15%]",
  "w-[20%]",
  "w-[25%]",
  "w-[30%]",
  "w-[35%]",
  "w-[40%]",
  "w-[45%]",
  "w-[50%]",
  "w-[55%]",
  "w-[60%]",
  "w-[65%]",
  "w-[70%]",
  "w-[75%]",
  "w-[80%]",
  "w-[85%]",
  "w-[90%]",
  "w-[95%]",
];

function progressWidthClass(pct: number): string {
  const step = Math.min(PROGRESS_WIDTH_CLASSES.length - 1, Math.max(0, Math.round(pct / 5) - 1));
  return PROGRESS_WIDTH_CLASSES[step] ?? "w-[5%]";
}

function formatTimer(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function WarmingCard({
  busy,
  revealed,
  autoRetryExhausted,
  onReady,
  onToggleProvisions,
}: {
  busy: boolean;
  revealed: boolean;
  autoRetryExhausted: boolean;
  onReady: (autoRetry: boolean) => void;
  onToggleProvisions: () => void;
}) {
  const [elapsedS, setElapsedS] = useState(0);
  const [estimateS, setEstimateS] = useState(DEFAULT_WARMING_ESTIMATE_S);
  const [cancelled, setCancelled] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const triggeredRef = useRef(false);
  const onReadyRef = useRef(onReady);
  useEffect(() => {
    onReadyRef.current = onReady;
  });

  useEffect(() => {
    if (cancelled || timedOut || autoRetryExhausted) return;
    let active = true;
    const ctl = new AbortController();
    const startedAt = Date.now();

    const checkHealth = async (allowAutoRetry: boolean) => {
      const h = await health(ctl.signal);
      if (!active || !h?.model) return;
      if (typeof h.model.estimated_ready_s === "number" && h.model.estimated_ready_s > 0) {
        setEstimateS(h.model.estimated_ready_s);
      }
      if (typeof h.model.warming_for_s === "number") {
        const serverElapsed = Math.floor(h.model.warming_for_s);
        setElapsedS((prev) => Math.max(prev, serverElapsed));
      }
      if (allowAutoRetry && h.model.ready && !h.model.warming && !triggeredRef.current) {
        triggeredRef.current = true;
        onReadyRef.current(true);
      }
    };

    void checkHealth(false);
    const pollId = window.setInterval(() => {
      if (Date.now() - startedAt >= WARMING_TIMEOUT_MS) {
        setTimedOut(true);
        return;
      }
      void checkHealth(true);
    }, WARMING_POLL_INTERVAL_MS);

    const tickId = window.setInterval(() => {
      setElapsedS((prev) => prev + 1);
    }, 1000);

    return () => {
      active = false;
      ctl.abort();
      window.clearInterval(pollId);
      window.clearInterval(tickId);
    };
  }, [cancelled, timedOut, autoRetryExhausted]);

  const pct = Math.min(95, Math.max(4, Math.round((elapsedS / Math.max(1, estimateS)) * 100)));
  const remainingMin = Math.max(1, Math.ceil((estimateS - elapsedS) / 60));
  const estimateNote = elapsedS >= estimateS ? "Almost ready" : `~${remainingMin} min remaining`;
  const stoppedAutoRetry = cancelled || timedOut || autoRetryExhausted;

  return (
    <section className="rounded-card border border-border bg-surface p-4" aria-live="polite">
      <h3 className="text-ui font-bold text-foreground">{WARMING_TITLE}</h3>
      <p className="mt-1.5 text-ui text-muted-foreground">
        {timedOut || autoRetryExhausted ? WARMING_TIMEOUT_BODY : WARMING_BODY}
      </p>

      {!stoppedAutoRetry && (
        <div className="mt-3 space-y-1.5">
          <div
            role="progressbar"
            aria-label={WARMING_TITLE}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            className="h-1.5 w-full overflow-hidden rounded-full bg-primary-soft"
          >
            <div
              className={cn(
                "h-full rounded-full bg-primary transition-[width] duration-[var(--dur-base)]",
                progressWidthClass(pct),
              )}
            />
          </div>
          <p className="text-caption text-muted-foreground tabular-nums">
            {formatTimer(elapsedS)} elapsed · {estimateNote}
          </p>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {stoppedAutoRetry ? (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => {
              triggeredRef.current = true;
              onReadyRef.current(false);
            }}
          >
            {ACTION_RETRY}
          </Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => setCancelled(true)}>
            {WARMING_CANCEL_LABEL}
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={onToggleProvisions}>
          {revealed ? ACTION_HIDE_PROVISIONS : ACTION_SHOW_PROVISIONS_WHILE_WAITING}
        </Button>
      </div>
    </section>
  );
}

function StreamingDots() {
  return (
    <span className="ml-1.5 inline-flex items-center gap-0.5 align-middle" aria-hidden="true">
      <span className="h-1 w-1 rounded-full bg-muted-foreground animate-pulse motion-reduce:animate-none" />
      <span className="h-1 w-1 rounded-full bg-muted-foreground animate-pulse [animation-delay:150ms] motion-reduce:animate-none" />
      <span className="h-1 w-1 rounded-full bg-muted-foreground animate-pulse [animation-delay:300ms] motion-reduce:animate-none" />
    </span>
  );
}

function Sentence({
  ev,
  turnId,
  showYear,
  onShowSource,
}: {
  ev: SentenceEvent;
  turnId: number;
  showYear: boolean;
  onShowSource: Props["onShowSource"];
}) {
  const refused = ev.sentence.verdict === "refused";
  return (
    <span className={cn("sentence-in", refused && "text-muted-foreground italic")}>
      {ev.sentence.text}
      {!refused &&
        ev.citations.map((c) => (
          <Citation key={c.passage_id} citation={c} turnId={turnId} showYear={showYear} onShow={onShowSource} />
        ))}{" "}
    </span>
  );
}

/** Whether a turn is a direct quote answer (`extractive` with reason `quote_requested`). */
export function isQuoteAnswer(turn: TurnData): boolean {
  return turn.extractive?.reason === "quote_requested";
}

export function Turn({ turn, busy, onReplace, onShowSource, onRevealProvisions }: Props) {
  const quoteGroupHeadingId = useId();
  const changeListId = useId();
  const changeBtnRef = useRef<HTMLButtonElement>(null);
  const [changing, setChanging] = useState(false);
  const [clarifyExpanded, setClarifyExpanded] = useState(false);
  const [changeExpanded, setChangeExpanded] = useState(false);
  const [localRevealed, setLocalRevealed] = useState(false);

  const answered = turn.sentences.length > 0;
  const quoteAnswer = isQuoteAnswer(turn);
  const fallbackExtractive = isFallbackExtractive(turn);
  const provisionsRevealed = onRevealProvisions
    ? Boolean(turn.provisionsRevealed)
    : localRevealed || Boolean(turn.provisionsRevealed);
  const showExtractiveBlocks = quoteAnswer || provisionsRevealed;
  const toggleProvisions = () => {
    if (onRevealProvisions) {
      onRevealProvisions(turn.id);
    } else {
      setLocalRevealed((v) => !v);
    }
  };

  const quoteLabels = quoteAnswer && turn.extractive ? groupQuoteLabels(turn.extractive.windows) : null;
  const emptyAnswer =
    turn.phase === "done" &&
    turn.windows.length > 0 &&
    !answered &&
    turn.extractive === null &&
    turn.error === null;

  const isWarmingFallback = turn.extractive?.reason === "provider_warming";
  const hasBudgetNotice = turn.notices.some((n) => n.code === "daily_cap" || n.code === "global_budget");
  const inlineNotices = turn.notices.filter(
    (n) => n.code !== "provider_fallback" && !(isWarmingFallback && n.code === "provider_warming"),
  );
  const instruments = new Set<string>();
  for (const ev of turn.sentences) {
    if (ev.sentence.verdict === "refused") continue;
    for (const c of ev.citations) {
      instruments.add(citationInstrumentKey(c.coordinate, c.label));
    }
  }
  const showCitationYear = instruments.size >= 2;

  const displayQuestion = turn.question ?? turn.query;
  const alternatives = turn.choice?.alternatives ?? [];

  function chooseLabel(label: string, nextAlternatives: LabelledCoordinate[]) {
    const citation = candidateCitation(label);
    onReplace({
      oldTurnId: turn.id,
      query: composeChoiceQuery(displayQuestion, citation),
      question: displayQuestion,
      choice: { citation, alternatives: nextAlternatives },
    });
  }

  return (
    <article
      className="scroll-mt-4"
      id={`turn-${turn.id}`}
      aria-labelledby={`turn-${turn.id}-q`}
    >
      <h2
        className={cn(
          "text-ui font-bold text-foreground focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          !turn.choice && "mb-3",
        )}
        id={`turn-${turn.id}-q`}
        tabIndex={-1}
      >
        <span className="sr-only">{QUESTION_LABEL}</span>
        {displayQuestion}
      </h2>

      {turn.choice && (
        <div
          className="mb-3 mt-1"
          onKeyDown={(e) => {
            if (changing && e.key === "Escape") {
              e.stopPropagation();
              setChanging(false);
              changeBtnRef.current?.focus();
            }
          }}
        >
          <div className="flex flex-wrap items-center gap-2 text-caption text-muted-foreground">
            <span>{usingCitationText(turn.choice.citation)}</span>
            {alternatives.length > 0 && (
              <button
                ref={changeBtnRef}
                type="button"
                disabled={busy}
                aria-expanded={changing}
                aria-controls={changeListId}
                onClick={() => setChanging((v) => !v)}
                className="cursor-pointer rounded-sm text-caption text-primary hover:underline underline-offset-4 disabled:cursor-default disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                {ACTION_CHANGE}
              </button>
            )}
          </div>
          {changing && alternatives.length > 0 && (
            <div id={changeListId} className="mt-2 space-y-2">
              <ul className="flex flex-wrap gap-2" aria-label={CLARIFY_ARIA_LABEL}>
                {(changeExpanded ? alternatives : alternatives.slice(0, INITIAL_CHANGE_COUNT)).map((c) => {
                  const citation = candidateCitation(c.label);
                  const isCurrent = citation === turn.choice?.citation;
                  return (
                    <li key={c.coordinate}>
                      <Chip
                        variant="outline"
                        selected={isCurrent}
                        disabled={busy || isCurrent}
                        onClick={() => {
                          setChanging(false);
                          chooseLabel(c.label, alternatives);
                        }}
                      >
                        {c.label}
                      </Chip>
                    </li>
                  );
                })}
              </ul>
              {alternatives.length > INITIAL_CHANGE_COUNT && (
                <button
                  type="button"
                  onClick={() => setChangeExpanded((v) => !v)}
                  className="cursor-pointer rounded-sm text-caption text-primary hover:underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  {changeExpanded
                    ? CLARIFY_SHOW_LESS
                    : CLARIFY_SHOW_MORE(alternatives.length - INITIAL_CHANGE_COUNT)}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {inlineNotices.length > 0 && (
        <ul className="mb-3 space-y-1.5" aria-label={NOTICES_ARIA_LABEL}>
          {inlineNotices.map((n, i) => (
            <li key={i}>
              <Notice notice={n} />
            </li>
          ))}
        </ul>
      )}

      {turn.clarify && (() => {
        const { question, candidates } = turn.clarify;
        const visibleCandidates = clarifyExpanded ? candidates : candidates.slice(0, INITIAL_CLARIFY_COUNT);
        const hiddenCount = candidates.length - INITIAL_CLARIFY_COUNT;
        return (
          <section
            className="mb-4 rounded-card border border-border bg-surface p-4"
            aria-label={CLARIFY_ARIA_LABEL}
          >
            <h3 className="text-ui font-bold text-foreground">{CLARIFY_TITLE}</h3>
            <p className="mt-1.5 text-ui text-muted-foreground">{question}</p>
            <ul className="mt-3 flex flex-wrap gap-2">
              {visibleCandidates.map((c) => (
                <li key={c.coordinate}>
                  <Chip
                    variant="outline"
                    disabled={busy}
                    onClick={() => chooseLabel(c.label, candidates)}
                  >
                    {c.label}
                  </Chip>
                </li>
              ))}
            </ul>
            {hiddenCount > 0 && (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => setClarifyExpanded((v) => !v)}
                  className="cursor-pointer rounded-sm text-caption text-primary hover:underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  {clarifyExpanded ? CLARIFY_SHOW_LESS : CLARIFY_SHOW_MORE(hiddenCount)}
                </button>
              </div>
            )}
          </section>
        );
      })()}

      {turn.refusal && (() => {
        const refusal = turn.refusal;
        const refusalAlternatives = refusal.suggestions.length > 1 ? refusal.suggestions : [];
        return (
          <RefusalCard
            refusal={refusal}
            onChoose={(label) => chooseLabel(label, refusalAlternatives)}
            disabled={busy}
          />
        );
      })()}

      {turn.extractive && (
        <section
          className="mb-4 space-y-3"
          aria-labelledby={quoteLabels?.groupHeading ? quoteGroupHeadingId : undefined}
          aria-label={quoteLabels?.groupHeading ? undefined : EXTRACTIVE_ARIA_LABEL}
        >
          {isWarmingFallback && turn.phase === "done" && (
            <WarmingCard
              busy={busy}
              revealed={provisionsRevealed}
              autoRetryExhausted={(turn.autoRetryCount ?? 0) >= MAX_WARMING_AUTO_RETRIES}
              onReady={(autoRetry) =>
                onReplace({
                  oldTurnId: turn.id,
                  query: turn.query,
                  question: displayQuestion,
                  choice: turn.choice ?? null,
                  ...(autoRetry ? { autoRetry: true } : {}),
                })
              }
              onToggleProvisions={toggleProvisions}
            />
          )}
          {fallbackExtractive && !isWarmingFallback && (
            <div className="space-y-2.5">
              {!hasBudgetNotice && (
                <Notice
                  notice={{
                    type: "notice",
                    code: turn.extractive.reason,
                    message: EXTRACTIVE_REASON[turn.extractive.reason],
                  }}
                />
              )}
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={toggleProvisions}>
                  {provisionsRevealed ? ACTION_HIDE_PROVISIONS : ACTION_SHOW_PROVISIONS}
                </Button>
                {!hasBudgetNotice && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      onReplace({
                        oldTurnId: turn.id,
                        query: turn.query,
                        question: displayQuestion,
                        choice: turn.choice ?? null,
                      })
                    }
                  >
                    {ACTION_RETRY}
                  </Button>
                )}
              </div>
            </div>
          )}
          {showExtractiveBlocks && (
            <>
              {quoteLabels?.groupHeading && (
                <h3 id={quoteGroupHeadingId} className="text-ui font-bold text-foreground tabular-nums">
                  {quoteLabels.groupHeading}
                </h3>
              )}
              {turn.extractive.windows.map((w, i) => (
                <QuoteBlock
                  key={w.passage_id}
                  window={w}
                  turnId={turn.id}
                  label={quoteLabels?.blockLabels[i]}
                  grouped={Boolean(quoteLabels?.groupHeading)}
                  allowCopy={quoteAnswer}
                  onShowSource={quoteAnswer ? undefined : onShowSource}
                />
              ))}
            </>
          )}
        </section>
      )}

      <div
        className="text-body text-foreground"
        aria-live="polite"
        aria-relevant="additions text"
        aria-busy={turn.phase === "streaming"}
      >
        {answered && (
          <p className="m-0">
            {turn.sentences.map((ev) => (
              <Sentence
                key={ev.sentence.index}
                ev={ev}
                turnId={turn.id}
                showYear={showCitationYear}
                onShowSource={onShowSource}
              />
            ))}
            {turn.phase === "streaming" && <StreamingDots />}
          </p>
        )}
      </div>

      {emptyAnswer && <p className="mt-2 text-ui text-muted-foreground">{EMPTY_ANSWER_TEXT}</p>}

      <StatusLine turn={turn} />

      {turn.error && (
        <section className="mt-3 rounded-card border border-border bg-surface p-4" role="alert">
          <h3 className="text-ui font-bold text-foreground">
            {turn.error.source === "server" ? ERROR_SERVER_TITLE : ERROR_CLIENT_TITLE}
          </h3>
          <p className="mt-1.5 text-ui text-muted-foreground">{turn.error.message}</p>
          <div className="mt-3">
            <Button
              variant="outline"
              size="md"
              disabled={busy}
              onClick={() =>
                onReplace({
                  oldTurnId: turn.id,
                  query: turn.query,
                  question: displayQuestion,
                  choice: turn.choice ?? null,
                })
              }
            >
              {ACTION_RETRY}
            </Button>
          </div>
        </section>
      )}

      {turn.phase === "stopped" && (
        <p className="mt-2 text-caption text-muted-foreground">{STOPPED_TEXT}</p>
      )}

      {turn.phase === "done" && hasAnswerContent(turn) && <ActionRow turn={turn} />}
    </article>
  );
}
