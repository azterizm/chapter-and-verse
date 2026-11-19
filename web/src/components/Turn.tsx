import type { SentenceEvent } from "../api/events";
import { type Turn as TurnData, windowAnchor } from "../state/reducer";
import { EXTRACTIVE_REASON, noticeTitle } from "../text";
import { Citation } from "./Citation";
import { ReceiptLine, TimingsLine } from "./Receipt";
import { RefusalCard } from "./RefusalCard";
import { WindowText } from "./WindowText";

interface Props {
  turn: TurnData;
  busy: boolean;
  onAsk(query: string): void;
  onShowSource(turnId: number, passageId: string): void;
}

function progress(turn: TurnData): string | null {
  if (turn.phase !== "streaming") return null;
  if (turn.route === null) return "Reading the citation.";
  if (turn.windows.length === 0) return "Finding the provisions.";
  if (turn.receipt === null) return "Checking each sentence against the provisions.";
  return "Finishing.";
}

function Sentence({ ev, turnId, onShowSource }: { ev: SentenceEvent; turnId: number; onShowSource: Props["onShowSource"] }) {
  const refused = ev.sentence.verdict === "refused";
  return (
    <span className={refused ? "sentence sentence-refused" : "sentence"}>
      {ev.sentence.text}
      {ev.citations.map((c) => (
        <Citation key={c.passage_id} citation={c} turnId={turnId} onShow={onShowSource} />
      ))}{" "}
    </span>
  );
}

export function Turn({ turn, busy, onAsk, onShowSource }: Props) {
  const status = progress(turn);
  const answered = turn.sentences.length > 0;
  const emptyAnswer =
    turn.phase === "done" && turn.windows.length > 0 && !answered && turn.extractive === null && turn.error === null;
  return (
    <article className="turn" id={`turn-${turn.id}`} aria-labelledby={`turn-${turn.id}-q`}>
      <h2 className="question" id={`turn-${turn.id}-q`}>
        <span className="visually-hidden">Question: </span>
        {turn.query}
      </h2>

      {turn.notices.length > 0 && (
        <ul className="notices" aria-label="Notices">
          {turn.notices.map((n, i) => (
            <li key={i} className={`notice notice-${n.code}`}>
              {noticeTitle(n.code) !== null && <strong className="notice-title">{noticeTitle(n.code)}. </strong>}
              {n.message}
            </li>
          ))}
        </ul>
      )}

      {turn.clarify && (
        <section className="card clarify" aria-label="Clarify">
          <p className="card-title">{turn.clarify.question}</p>
          <ul className="chips">
            {turn.clarify.candidates.map((c) => (
              <li key={c.coordinate}>
                <button type="button" className="chip" disabled={busy} onClick={() => onAsk(c.coordinate)}>
                  {c.label}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {turn.refusal && <RefusalCard refusal={turn.refusal} onAsk={onAsk} disabled={busy} />}

      {turn.extractive && (
        <section className="extractive" aria-label="Provision text">
          <div className="banner" role="note">
            <p className="card-title">No written answer</p>
            <p>{EXTRACTIVE_REASON[turn.extractive.reason]}</p>
          </div>
          {turn.extractive.windows.map((w) => (
            <blockquote key={w.passage_id} className="extract" cite={w.source_url}>
              <p className="extract-heading">
                <a
                  href={`#${windowAnchor(turn.id, w.passage_id)}`}
                  onClick={(e) => {
                    e.preventDefault();
                    onShowSource(turn.id, w.passage_id);
                  }}
                >
                  {w.heading}
                </a>
              </p>
              <WindowText text={w.text} highlight={w.highlight} />
            </blockquote>
          ))}
        </section>
      )}

      <div className="answer" aria-live="polite" aria-relevant="additions text" aria-busy={turn.phase === "streaming"}>
        {answered && (
          <p className="answer-text">
            {turn.sentences.map((ev) => (
              <Sentence key={ev.sentence.index} ev={ev} turnId={turn.id} onShowSource={onShowSource} />
            ))}
          </p>
        )}
      </div>

      {emptyAnswer && <p className="quiet">No point could be stated reliably from these provisions.</p>}

      {status && (
        <p className="progress" role="status">
          {status}
        </p>
      )}

      {turn.error && (
        <section className="card error" role="alert">
          <p className="card-title">{turn.error.source === "server" ? "The server stopped this answer" : "This answer failed"}</p>
          <p>{turn.error.message}</p>
        </section>
      )}

      {turn.phase === "stopped" && <p className="quiet">Stopped.</p>}

      {(turn.receipt || turn.timings) && (
        <footer className="turn-foot">
          {turn.receipt && <ReceiptLine receipt={turn.receipt} />}
          {turn.timings && <TimingsLine timings={turn.timings} />}
        </footer>
      )}
    </article>
  );
}
