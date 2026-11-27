import { useEffect, useRef, useState } from "react";
import { getResolvedTheme, type ResolvedTheme } from "../lib/theme";
import { cn } from "../lib/utils";
import { TURNSTILE_PANEL_LABEL } from "../text";

// Cloudflare Turnstile, loaded only when VITE_TURNSTILE_SITE_KEY is set. The script comes from
// challenges.cloudflare.com, which the CSP allows; nothing else is loaded from outside.

interface TurnstileApi {
  render(el: HTMLElement, opts: Record<string, unknown>): string;
  execute(elOrId?: HTMLElement | string): void;
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
  appearance: "interaction-only";
  execution: "execute";
  size: TurnstileSize;
  theme: ResolvedTheme;
  callback: (token: string) => void;
  "expired-callback": () => void;
  "timeout-callback": () => void;
  "error-callback": () => void;
  "unsupported-callback": () => void;
  "before-interactive-callback": () => void;
  "after-interactive-callback": () => void;
  [key: string]: unknown;
}

export function turnstileOptions(
  onToken: (token: string | null) => void,
  onError: (message: string) => void,
  onTimeout?: () => void,
  containerWidth = 0,
  theme: ResolvedTheme = getResolvedTheme(),
  onBeforeInteractive?: () => void,
  onAfterInteractive?: () => void,
): TurnstileRenderOptions {
  return {
    sitekey: TURNSTILE_SITE_KEY,
    action: TURNSTILE_ACTION,
    appearance: "interaction-only",
    execution: "execute",
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
    "before-interactive-callback": () => onBeforeInteractive?.(),
    "after-interactive-callback": () => onAfterInteractive?.(),
  };
}

export interface TurnstileGateOptions {
  timeoutMs?: number;
  onSend: (query: string, token: string | null) => void;
  onExecute?: () => void;
  onReset: () => void;
  onStatusChange: (status: string | null) => void;
  onNeedVisible?: (visible: boolean) => void;
  onError: (error: string) => void;
}

export class TurnstileGate {
  private token: string | null = null;
  private pendingQuery: string | null = null;
  private retried = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private readonly timeoutMs: number;
  private readonly onSend: (query: string, token: string | null) => void;
  private readonly onExecute: (() => void) | undefined;
  private readonly onReset: () => void;
  private readonly onStatusChange: (status: string | null) => void;
  private readonly onNeedVisible: ((visible: boolean) => void) | undefined;
  private readonly onError: (error: string) => void;

  constructor(opts: TurnstileGateOptions) {
    this.timeoutMs = opts.timeoutMs ?? TURNSTILE_TIMEOUT_MS;
    this.onSend = opts.onSend;
    this.onExecute = opts.onExecute;
    this.onReset = opts.onReset;
    this.onStatusChange = opts.onStatusChange;
    this.onNeedVisible = opts.onNeedVisible;
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
      this.onNeedVisible?.(false);
      this.onReset();
      this.onSend(query, tok);
      return;
    }
    this.pendingQuery = query;
    this.retried = false;
    this.onStatusChange(TURNSTILE_CHECKING_MESSAGE);
    this.onExecute?.();
    this.clearTimers();
    this.timer = setTimeout(() => this.handleTimeout(), this.timeoutMs);
  }

  onBeforeInteractive(): void {
    this.onNeedVisible?.(true);
  }

  onAfterInteractive(): void {
    this.onNeedVisible?.(false);
  }

  onToken(token: string | null): void {
    if (!token) {
      this.token = null;
      return;
    }
    if (this.pendingQuery !== null) {
      const q = this.pendingQuery;
      this.pendingQuery = null;
      this.clearTimers();
      this.onNeedVisible?.(false);
      this.onStatusChange(null);
      this.token = null;
      this.onReset();
      this.onSend(q, token);
      return;
    }
    this.token = token;
    this.onNeedVisible?.(false);
  }

  onTimeout(): void {
    if (this.pendingQuery !== null) {
      this.handleTimeout();
    } else {
      this.token = null;
      this.onNeedVisible?.(false);
      this.onReset();
    }
  }

  private handleTimeout(): void {
    if (this.pendingQuery === null) return;
    if (!this.retried) {
      this.retried = true;
      this.onReset();
      this.onExecute?.();
      this.clearTimer();
      this.timer = setTimeout(() => this.handleTimeout(), this.timeoutMs);
      return;
    }
    this.clearTimers();
    this.pendingQuery = null;
    this.onNeedVisible?.(false);
    this.onStatusChange(null);
    this.onError(TURNSTILE_TIMEOUT_MESSAGE);
  }

  onFailure(message: string): void {
    this.clearTimers();
    this.pendingQuery = null;
    this.onNeedVisible?.(false);
    this.onStatusChange(null);
    this.onError(message);
  }

  cancel(): void {
    this.clearTimers();
    this.pendingQuery = null;
    this.onNeedVisible?.(false);
    this.onStatusChange(null);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private clearTimers(): void {
    this.clearTimer();
  }
}

interface Props {
  /** Bumped by the parent to ask for a fresh token. */
  resetKey: number;
  /** Bumped by the parent when a request needs verification. */
  executeKey: number;
  /** Shown as a quiet side card only when interactive verification is needed. */
  visible: boolean;
  onToken(token: string | null): void;
  onError(message: string): void;
  onTimeout?(): void;
  onBeforeInteractive?(): void;
  onAfterInteractive?(): void;
}

export function Turnstile({
  resetKey,
  executeKey,
  visible,
  onToken,
  onError,
  onTimeout,
  onBeforeInteractive,
  onAfterInteractive,
}: Props) {
  const el = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);
  const pendingExecute = useRef(false);
  const lastExecutedKey = useRef(0);
  const lastResetKey = useRef(0);
  const [size, setSize] = useState<TurnstileSize>("flexible");
  const handlers = useRef({ onToken, onError, onTimeout, onBeforeInteractive, onAfterInteractive });
  useEffect(() => {
    handlers.current = { onToken, onError, onTimeout, onBeforeInteractive, onAfterInteractive };
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
          () => handlers.current.onBeforeInteractive?.(),
          () => handlers.current.onAfterInteractive?.(),
        );
        setSize(opts.size);
        widget.current = ts.render(el.current, opts);
        if (pendingExecute.current && widget.current) {
          pendingExecute.current = false;
          ts.execute(widget.current);
        }
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
    if (resetKey > lastResetKey.current) {
      lastResetKey.current = resetKey;
      if (widget.current && typeof window !== "undefined" && window.turnstile) {
        handlers.current.onToken(null);
        window.turnstile.reset(widget.current);
      }
    }
  }, [resetKey]);

  useEffect(() => {
    if (executeKey > lastExecutedKey.current) {
      lastExecutedKey.current = executeKey;
      if (widget.current && typeof window !== "undefined" && window.turnstile) {
        window.turnstile.execute(widget.current);
      } else {
        pendingExecute.current = true;
      }
    }
  }, [executeKey]);

  return (
    <div
      data-turnstile-panel=""
      aria-hidden={!visible}
      className={cn(
        "fixed bottom-4 right-4 z-40 w-[320px] max-w-[calc(100vw-2rem)] rounded-card border border-border bg-surface p-3 shadow-card transition-all duration-[var(--dur-base)] print:hidden",
        visible
          ? "translate-y-0 opacity-100 pointer-events-auto"
          : "translate-y-2 opacity-0 pointer-events-none",
      )}
    >
      <p className="mb-2 text-caption text-muted-foreground">{TURNSTILE_PANEL_LABEL}</p>
      <div
        className={cn("w-full", size === "compact" ? "min-h-[140px]" : "min-h-[65px]")}
        ref={el}
      />
    </div>
  );
}
