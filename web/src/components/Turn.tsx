import { useId } from "react";
import type { SentenceEvent } from "../api/events";
import { cn } from "../lib/utils";
import type { Turn as TurnData } from "../state/reducer";
import {
  ACTION_RETRY,
  citationInstrumentKey,
  CLARIFY_ARIA_LABEL,
  CLARIFY_TITLE,
  EMPTY_ANSWER_TEXT,
  ERROR_CLIENT_TITLE,
  ERROR_SERVER_TITLE,
  EXTRACTIVE_ARIA_LABEL,
  EXTRACTIVE_REASON,
  groupQuoteLabels,
  NOTICES_ARIA_LABEL,
  QUESTION_LABEL,
  STOPPED_TEXT,
} from "../text";
import { ActionRow } from "./ActionRow";
import { Citation } from "./Citation";
import { Notice } from "./Notice";
import { QuoteBlock } from "./QuoteBlock";
import { RefusalCard } from "./RefusalCard";
import { StatusLine } from "./StatusLine";
import { Button } from "./ui/button";
import { Chip } from "./ui/chip";

interface Props {
  turn: TurnData;
  busy: boolean;
  onAsk(query: string): void;
  onShowSource(turnId: number, passageId: string): void;
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

export function Turn({ turn, busy, onAsk, onShowSource }: Props) {
  const quoteGroupHeadingId = useId();
  const answered = turn.sentences.length > 0;
  const quoteAnswer = isQuoteAnswer(turn);
  const quoteLabels = quoteAnswer && turn.extractive ? groupQuoteLabels(turn.extractive.windows) : null;
  const emptyAnswer =
    turn.phase === "done" &&
    turn.windows.length > 0 &&
    !answered &&
    turn.extractive === null &&
    turn.error === null;

  const inlineNotices = turn.notices.filter((n) => n.code !== "provider_fallback");
  const instruments = new Set<string>();
  for (const ev of turn.sentences) {
    if (ev.sentence.verdict === "refused") continue;
    for (const c of ev.citations) {
      instruments.add(citationInstrumentKey(c.coordinate, c.label));
    }
  }
  const showCitationYear = instruments.size >= 2;

  return (
    <article
      className="mb-7 scroll-mt-4 border-b border-border pb-7"
      id={`turn-${turn.id}`}
      aria-labelledby={`turn-${turn.id}-q`}
    >
      <h2 className="mb-3 text-ui font-bold text-foreground" id={`turn-${turn.id}-q`}>
        <span className="sr-only">{QUESTION_LABEL}</span>
        {turn.query}
      </h2>

      {inlineNotices.length > 0 && (
        <ul className="mb-3 space-y-1.5" aria-label={NOTICES_ARIA_LABEL}>
          {inlineNotices.map((n, i) => (
            <li key={i}>
              <Notice notice={n} />
            </li>
          ))}
        </ul>
      )}

      {turn.clarify && (
        <section
          className="mb-4 rounded-card border border-border bg-surface p-4"
          aria-label={CLARIFY_ARIA_LABEL}
        >
          <h3 className="text-ui font-bold text-foreground">{CLARIFY_TITLE}</h3>
          <p className="mt-1.5 text-ui text-muted-foreground">{turn.clarify.question}</p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {turn.clarify.candidates.map((c) => (
              <li key={c.coordinate}>
                <Chip variant="outline" disabled={busy} onClick={() => onAsk(c.coordinate)}>
                  {c.label}
                </Chip>
              </li>
            ))}
          </ul>
        </section>
      )}

      {turn.refusal && <RefusalCard refusal={turn.refusal} onAsk={onAsk} disabled={busy} />}

      {turn.extractive && (
        <section
          className="mb-4 space-y-3"
          aria-labelledby={quoteLabels?.groupHeading ? quoteGroupHeadingId : undefined}
          aria-label={quoteLabels?.groupHeading ? undefined : EXTRACTIVE_ARIA_LABEL}
        >
          {!quoteAnswer && (
            <Notice
              notice={{
                type: "notice",
                code: turn.extractive.reason,
                message: EXTRACTIVE_REASON[turn.extractive.reason],
              }}
            />
          )}
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
              onClick={() => onAsk(turn.query)}
            >
              {ACTION_RETRY}
            </Button>
          </div>
        </section>
      )}

      {turn.phase === "stopped" && (
        <p className="mt-2 text-caption text-muted-foreground">{STOPPED_TEXT}</p>
      )}

      {turn.phase === "done" && <ActionRow turn={turn} />}
    </article>
  );
}
