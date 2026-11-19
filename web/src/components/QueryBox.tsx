import { type FormEvent, type KeyboardEvent, useState } from "react";
import { byteLength, MAX_QUERY_BYTES } from "../api/client";

interface Props {
  busy: boolean;
  /** Why the box cannot send yet (the bot check), or null. */
  blocked: string | null;
  onAsk(query: string): void;
  onStop(): void;
  inputRef: React.RefObject<HTMLTextAreaElement>;
}

export function QueryBox({ busy, blocked, onAsk, onStop, inputRef }: Props) {
  const [text, setText] = useState("");
  const bytes = byteLength(text);
  const over = bytes > MAX_QUERY_BYTES;
  const empty = text.trim() === "";
  const canSend = !busy && !over && !empty && blocked === null;

  const send = () => {
    if (!canSend) return;
    onAsk(text.trim());
    setText("");
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    send();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  return (
    <form className="query" onSubmit={onSubmit} aria-label="Ask a question">
      <label htmlFor="q" className="query-label">
        Ask about UK legislation
      </label>
      <textarea
        id="q"
        ref={inputRef}
        className="query-input"
        rows={3}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        aria-describedby="q-help q-count"
        aria-invalid={over}
        spellCheck
        autoComplete="off"
      />
      <div className="query-row">
        <p id="q-help" className="query-help">
          Enter sends. Shift+Enter adds a line. Do not enter client details or personal data.
        </p>
        <p id="q-count" className={over ? "query-count over" : "query-count"} aria-live={over ? "polite" : "off"}>
          {bytes} / {MAX_QUERY_BYTES} bytes{over ? ". Too long." : ""}
        </p>
      </div>
      <div className="query-actions">
        {busy ? (
          <button type="button" className="button" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="submit" className="button button-primary" disabled={!canSend}>
            Ask
          </button>
        )}
        {blocked && <span className="quiet small">{blocked}</span>}
      </div>
    </form>
  );
}
