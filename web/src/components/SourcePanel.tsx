import type { Window } from "../api/events";
import { panelWindows, type Turn, windowAnchor } from "../state/reducer";
import { formatDate } from "../text";
import { ExternalIcon } from "./ExternalIcon";
import { StatusBadge } from "./StatusBadge";
import { WindowText } from "./WindowText";

function SourceWindow({ w, turnId }: { w: Window; turnId: number }) {
  return (
    <article className="window" id={windowAnchor(turnId, w.passage_id)} tabIndex={-1} aria-labelledby={`${windowAnchor(turnId, w.passage_id)}-h`}>
      <div className="window-meta">
        <span className="pid">{w.passage_id}</span>
        <StatusBadge status={w.status} />
      </div>
      <h3 className="window-heading" id={`${windowAnchor(turnId, w.passage_id)}-h`}>
        {w.heading}
      </h3>
      <p className="window-coord">
        <code>{w.coordinate}</code>
        {w.version_date && <span> Version of {formatDate(w.version_date)}.</span>}
      </p>
      <WindowText text={w.text} highlight={w.highlight} />
      <p className="window-link">
        <a href={w.source_url} target="_blank" rel="noopener noreferrer">
          Read on legislation.gov.uk
          <span className="visually-hidden"> (opens in a new tab)</span>
          <ExternalIcon />
        </a>
      </p>
    </article>
  );
}

export function SourcePanel({ turn }: { turn: Turn | null }) {
  const windows = turn ? panelWindows(turn) : [];
  let empty: string | null = null;
  if (turn === null) empty = "The provisions an answer is built from appear here.";
  else if (windows.length === 0)
    empty = turn.phase === "streaming" ? "Finding the provisions." : "No provision text for this question.";
  return (
    <aside className="sources" aria-labelledby="sources-h">
      <h2 id="sources-h" className="panel-title">
        Sources
      </h2>
      {turn && windows.length > 0 && <p className="sources-for">For: {turn.query}</p>}
      {turn === null || empty !== null ? (
        <p className="quiet">{empty}</p>
      ) : (
        windows.map((w) => <SourceWindow key={w.passage_id} w={w} turnId={turn.id} />)
      )}
    </aside>
  );
}
