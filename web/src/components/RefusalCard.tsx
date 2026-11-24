import { ExternalLink } from "lucide-react";
import type { RefusalEvent } from "../api/events";
import { OPENS_NEW_TAB, REFUSAL_LINK_LABEL, refusalSnapshotText, refusalTitle, SUGGESTIONS_LABEL } from "../text";
import { Chip } from "./ui/chip";

interface Props {
  refusal: RefusalEvent;
  onAsk(query: string): void;
  disabled: boolean;
}

/** Single-card refusal message with optional suggestion chips and legislation.gov.uk link. */
export function RefusalCard({ refusal, onAsk, disabled }: Props) {
  const kind = refusal.kind.toLowerCase().replace(/_/g, "-");
  const title = refusalTitle(refusal.kind);
  return (
    <section
      className="mb-4 rounded-card border border-border bg-surface p-4"
      aria-label={title}
    >
      <h3 className="text-ui font-bold text-foreground">{title}</h3>
      <p className="mt-1.5 text-ui text-muted-foreground">{refusal.message}</p>
      {refusal.suggestions.length > 0 && (
        <div className="mt-3">
          <p className="text-caption text-muted-foreground" id={`sugg-${kind}`}>
            {SUGGESTIONS_LABEL}
          </p>
          <ul className="mt-2 flex flex-wrap gap-2" aria-labelledby={`sugg-${kind}`}>
            {refusal.suggestions.map((s) => (
              <li key={s.coordinate}>
                <Chip variant="outline" disabled={disabled} onClick={() => onAsk(s.coordinate)}>
                  {s.label}
                </Chip>
              </li>
            ))}
          </ul>
        </div>
      )}
      {refusal.link && (
        <p className="mt-3">
          <a
            href={refusal.link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-caption text-primary hover:underline underline-offset-4"
          >
            {REFUSAL_LINK_LABEL}
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="sr-only">{OPENS_NEW_TAB}</span>
          </a>
        </p>
      )}
      <p className="mt-3 text-caption text-muted-foreground">{refusalSnapshotText(refusal.snapshot)}</p>
    </section>
  );
}
