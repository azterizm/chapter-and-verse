import { useEffect, useRef, useState } from "react";
import { getResolvedTheme, type ResolvedTheme } from "../lib/theme";
import { cn } from "../lib/utils";

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
export const TURNSTILE_ACTION = "ask";
export const TURNSTILE_TIMEOUT_MS = 30000;
export const TURNSTILE_TIMEOUT_MESSAGE =
  "The bot check did not finish. Click Ask again, or reload the page if it keeps failing.";
export const TURNSTILE_UNSUPPORTED_MESSAGE = "This browser cannot run the bot check. Try another browser.";
export const TURNSTILE_CHECKING_MESSAGE = "Checking...";

const SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

let loading: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (typeof window !== "undefined" && window.turnstile) return Promise.resolve(window.turnstile);
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

export const TURNSTILE_FLEXIBLE_MIN_WIDTH = 300;

export type TurnstileSize = "compact" | "flexible";

export function turnstileSize(containerWidth: number): TurnstileSize {
  return containerWidth >= TURNSTILE_FLEXIBLE_MIN_WIDTH ? "flexible" : "compact";
}

function measureWidth(el: HTMLElement): number {
  return el.getBoundingClientRect().width || el.clientWidth;
}

export interface TurnstileRenderOptions {
  sitekey: string;
  action: string;
  appearance: "always";
  size: TurnstileSize;
  theme: ResolvedTheme;
  callback: (token: string) => void;
  "expired-callback": () => void;
  "timeout-callback": () => void;
  "error-callback": () => void;
  "unsupported-callback": () => void;
  [key: string]: unknown;
}

export function turnstileOptions(
  onToken: (token: string | null) => void,
  onError: (message: string) => void,
  onTimeout?: () => void,
  containerWidth = 0,
  theme: ResolvedTheme = getResolvedTheme(),
): TurnstileRenderOptions {
  return {
    sitekey: TURNSTILE_SITE_KEY,
    action: TURNSTILE_ACTION,
    appearance: "always",
    size: turnstileSize(containerWidth),
    theme,
    callback: (token: string) => onToken(token),
    "expired-callback": () => onToken(null),
    "timeout-callback": () => {
      if (onTimeout) {
        onTimeout();
      } else {
        onToken(null);
      }
    },
    "error-callback": () => onError("The bot check failed to run. Reload the page."),
    "unsupported-callback": () => onError(TURNSTILE_UNSUPPORTED_MESSAGE),
  };
}

export interface TurnstileGateOptions {
  timeoutMs?: number;
  onSend: (query: string, token: string | null) => void;
  onReset: () => void;
  onStatusChange: (status: string | null) => void;
  onError: (error: string) => void;
}

export class TurnstileGate {
  private token: string | null = null;
  private pendingQuery: string | null = null;
  private retried = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly timeoutMs: number;
  private readonly onSend: (query: string, token: string | null) => void;
  private readonly onReset: () => void;
  private readonly onStatusChange: (status: string | null) => void;
  private readonly onError: (error: string) => void;

  constructor(opts: TurnstileGateOptions) {
    this.timeoutMs = opts.timeoutMs ?? TURNSTILE_TIMEOUT_MS;
    this.onSend = opts.onSend;
    this.onReset = opts.onReset;
    this.onStatusChange = opts.onStatusChange;
    this.onError = opts.onError;
  }

  isWaiting(): boolean {
    return this.pendingQuery !== null;
  }

  getToken(): string | null {
    return this.token;
  }

  ask(query: string, turnstileEnabled = true): void {
    if (!turnstileEnabled) {
      this.onSend(query, null);
      return;
    }
    if (this.token) {
      const tok = this.token;
      this.token = null;
      this.onReset();
      this.onSend(query, tok);
      return;
    }
    this.pendingQuery = query;
    this.retried = false;
    this.onStatusChange(TURNSTILE_CHECKING_MESSAGE);
    this.clearTimer();
    this.timer = setTimeout(() => this.handleTimeout(), this.timeoutMs);
  }

  onToken(token: string | null): void {
    if (!token) {
      this.token = null;
      return;
    }
    if (this.pendingQuery !== null) {
      const q = this.pendingQuery;
      this.pendingQuery = null;
      this.clearTimer();
      this.onStatusChange(null);
      this.token = null;
      this.onReset();
      this.onSend(q, token);
      return;
    }
    this.token = token;
  }

  onTimeout(): void {
    if (this.pendingQuery !== null) {
      this.handleTimeout();
    } else {
      this.token = null;
      this.onReset();
    }
  }

  private handleTimeout(): void {
    if (this.pendingQuery === null) return;
    if (!this.retried) {
      this.retried = true;
      this.onReset();
      this.clearTimer();
      this.timer = setTimeout(() => this.handleTimeout(), this.timeoutMs);
      return;
    }
    this.clearTimer();
    this.pendingQuery = null;
    this.onStatusChange(null);
    this.onError(TURNSTILE_TIMEOUT_MESSAGE);
  }

  onFailure(message: string): void {
    this.clearTimer();
    this.pendingQuery = null;
    this.onStatusChange(null);
    this.onError(message);
  }

  cancel(): void {
    this.clearTimer();
    this.pendingQuery = null;
    this.onStatusChange(null);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

interface Props {
  /** Bumped by the parent to ask for a fresh token. */
  resetKey: number;
  onToken(token: string | null): void;
  onError(message: string): void;
  onTimeout?(): void;
}

export function Turnstile({ resetKey, onToken, onError, onTimeout }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const [size, setSize] = useState<TurnstileSize>("flexible");
  const handlers = useRef({ onToken, onError, onTimeout });
  useEffect(() => {
    handlers.current = { onToken, onError, onTimeout };
  });

  useEffect(() => {
    let live = true;
    loadTurnstile().then(
      (ts) => {
        if (!live || !el.current) return;
        const opts = turnstileOptions(
          (token) => handlers.current.onToken(token),
          (message) => handlers.current.onError(message),
          () => handlers.current.onTimeout?.(),
          measureWidth(el.current),
          getResolvedTheme(),
        );
        setSize(opts.size);
        widget.current = ts.render(el.current, opts);
      },
      () => live && handlers.current.onError("The bot check could not load. Reload the page."),
    );
    return () => {
      live = false;
      if (widget.current && typeof window !== "undefined" && window.turnstile) {
        window.turnstile.remove(widget.current);
      }
      widget.current = null;
    };
  }, []);

  useEffect(() => {
    if (resetKey > 0 && widget.current && typeof window !== "undefined" && window.turnstile) {
      handlers.current.onToken(null);
      window.turnstile.reset(widget.current);
    }
  }, [resetKey]);

  return (
    <div
      className={cn("mb-2 w-full", size === "compact" ? "min-h-[140px]" : "min-h-[65px]")}
      ref={el}
    />
  );
}
