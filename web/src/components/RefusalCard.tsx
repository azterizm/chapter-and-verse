import type { RefusalEvent } from "../api/events";
import { formatDate, refusalTitle } from "../text";
import { ExternalIcon } from "./ExternalIcon";

interface Props {
  refusal: RefusalEvent;
  onAsk(query: string): void;
  disabled: boolean;
}

export function RefusalCard({ refusal, onAsk, disabled }: Props) {
  const kind = refusal.kind.toLowerCase().replace(/_/g, "-");
  return (
    <section className={`card refusal refusal-${kind}`} aria-label={refusalTitle(refusal.kind)}>
      <h3 className="card-title">{refusalTitle(refusal.kind)}</h3>
      <p>{refusal.message}</p>
      {refusal.suggestions.length > 0 && (
        <div className="chip-group">
          <p className="chip-group-label" id={`sugg-${kind}`}>
            Did you mean
          </p>
          <ul className="chips" aria-labelledby={`sugg-${kind}`}>
            {refusal.suggestions.map((s) => (
              <li key={s.coordinate}>
                <button type="button" className="chip" disabled={disabled} onClick={() => onAsk(s.coordinate)}>
                  {s.label}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {refusal.link && (
        <p>
          <a href={refusal.link} target="_blank" rel="noopener noreferrer">
            Read it on legislation.gov.uk
            <span className="visually-hidden"> (opens in a new tab)</span>
            <ExternalIcon />
          </a>
        </p>
      )}
      <p className="quiet small">Corpus snapshot {formatDate(refusal.snapshot)}.</p>
    </section>
  );
}
