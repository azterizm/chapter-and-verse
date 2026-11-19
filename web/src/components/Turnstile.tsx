import { useEffect, useRef } from "react";

// Cloudflare Turnstile, loaded only when VITE_TURNSTILE_SITE_KEY is set. The script comes from
// challenges.cloudflare.com, which the CSP allows; nothing else is loaded from outside.

interface TurnstileApi {
  render(el: HTMLElement, opts: Record<string, unknown>): string;
  reset(id?: string): void;
  remove(id?: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

export const TURNSTILE_SITE_KEY: string = import.meta.env.VITE_TURNSTILE_SITE_KEY ?? "";
const SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

let loading: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT_URL;
    s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("turnstile missing")));
    s.onerror = () => {
      loading = null;
      reject(new Error("turnstile failed to load"));
    };
    document.head.appendChild(s);
  });
  return loading;
}

interface Props {
  /** Bumped by the parent to ask for a fresh token. */
  resetKey: number;
  onToken(token: string | null): void;
  onError(message: string): void;
}

export function Turnstile({ resetKey, onToken, onError }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const handlers = useRef({ onToken, onError });
  useEffect(() => {
    handlers.current = { onToken, onError };
  });

  useEffect(() => {
    let live = true;
    loadTurnstile().then(
      (ts) => {
        if (!live || !el.current) return;
        widget.current = ts.render(el.current, {
          sitekey: TURNSTILE_SITE_KEY,
          appearance: "interaction-only",
          callback: (token: string) => handlers.current.onToken(token),
          "expired-callback": () => handlers.current.onToken(null),
          "error-callback": () => handlers.current.onError("The bot check failed to run. Reload the page."),
        });
      },
      () => live && handlers.current.onError("The bot check could not load. Reload the page."),
    );
    return () => {
      live = false;
      if (widget.current && window.turnstile) window.turnstile.remove(widget.current);
      widget.current = null;
    };
  }, []);

  useEffect(() => {
    if (resetKey > 0 && widget.current && window.turnstile) {
      handlers.current.onToken(null);
      window.turnstile.reset(widget.current);
    }
  }, [resetKey]);

  return <div className="turnstile" ref={el} />;
}
