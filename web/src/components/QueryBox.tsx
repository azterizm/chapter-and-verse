import { ArrowUp } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { byteLength, MAX_QUERY_BYTES } from "../api/client";
import {
  COMPOSER_FORM_LABEL,
  COMPOSER_PLACEHOLDER,
  COMPOSER_TOO_LONG,
  SEND_LABEL,
} from "../text";
import { TURNSTILE_TIMEOUT_MESSAGE } from "./Turnstile";
import { Button } from "./ui/button";

export interface QueryPrefill {
  text: string;
  seq: number;
}

interface Props {
  busy: boolean;
  /** Why the box cannot send yet (the bot check), or null. */
  blocked: string | null;
  onAsk(query: string): void;
  inputRef: React.RefObject<HTMLTextAreaElement>;
  prefill?: QueryPrefill | null;
}

// 6 lines of text-body (27px line-height) + 16px vertical padding (py-2).
const MAX_COMPOSER_HEIGHT_PX = 6 * 27 + 16;

export function QueryBox({ busy, blocked, onAsk, inputRef, prefill }: Props) {
  const [text, setText] = useState("");
  const lastSubmitted = useRef("");
  const lastPrefillSeq = useRef(0);

  const bytes = byteLength(text);
  const over = bytes > MAX_QUERY_BYTES;
  const empty = text.trim() === "";
  const isBlocking = blocked !== null && blocked !== TURNSTILE_TIMEOUT_MESSAGE;
  const canSend = !busy && !over && !empty && !isBlocking;

  const resize = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const measured = el.scrollHeight;
    if (measured > 0) {
      el.style.height = `${Math.min(measured, MAX_COMPOSER_HEIGHT_PX)}px`;
    }
    el.style.overflowY = measured > MAX_COMPOSER_HEIGHT_PX ? "auto" : "hidden";
  }, [inputRef]);

  useEffect(() => {
    resize();
  }, [text, resize]);

  useEffect(() => {
    if (prefill && prefill.seq !== lastPrefillSeq.current) {
      lastPrefillSeq.current = prefill.seq;
      setText(prefill.text);
      const el = inputRef.current;
      if (el) {
        el.focus();
      }
    }
  }, [prefill, inputRef]);

  useEffect(() => {
    if (blocked === TURNSTILE_TIMEOUT_MESSAGE && text === "" && lastSubmitted.current !== "") {
      setText(lastSubmitted.current);
    }
  }, [blocked, text]);

  const send = () => {
    if (!canSend) return;
    const trimmed = text.trim();
    lastSubmitted.current = trimmed;
    onAsk(trimmed);
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

  const statusId = over || blocked ? "q-status" : undefined;

  return (
    <form onSubmit={onSubmit} aria-label={COMPOSER_FORM_LABEL}>
      <div className="flex items-end gap-2 rounded-[30px] border border-border bg-surface p-2 pl-4 shadow-card transition-colors focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-ring">
        <label htmlFor="q" className="sr-only">
          {COMPOSER_PLACEHOLDER}
        </label>
        <textarea
          id="q"
          ref={inputRef}
          rows={1}
          value={text}
          placeholder={COMPOSER_PLACEHOLDER}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          aria-describedby={statusId}
          aria-invalid={over}
          spellCheck
          autoComplete="off"
          className="min-h-11 max-h-[178px] w-full flex-1 resize-none bg-transparent py-2 text-body text-foreground placeholder:text-muted-foreground focus:outline-none focus-visible:outline-none"
        />
        <Button type="submit" variant="primary" size="icon" disabled={!canSend} aria-label={SEND_LABEL}>
          <ArrowUp className="h-5 w-5" aria-hidden="true" />
        </Button>
      </div>
      {(over || blocked) && (
        <p id="q-status" role="status" aria-live="polite" className="mt-2 px-4 text-caption text-muted-foreground">
          {over ? COMPOSER_TOO_LONG : blocked}
        </p>
      )}
    </form>
  );
}
