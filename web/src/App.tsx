import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState, useSyncExternalStore } from "react";
import { type AskRequest, ask, health, newSessionId } from "./api/client";
import { Footer } from "./components/Footer";
import { QueryBox, type QueryPrefill } from "./components/QueryBox";
import { SourcePanel } from "./components/SourcePanel";
import { Turn } from "./components/Turn";
import {
  Turnstile,
  TURNSTILE_SITE_KEY,
  TurnstileGate,
  TURNSTILE_TIMEOUT_MESSAGE,
} from "./components/Turnstile";
import { Chip } from "./components/ui/chip";
import { ToastProvider } from "./components/ui/toast";
import { TooltipProvider } from "./components/ui/tooltip";
import { cn } from "./lib/utils";
import { initialState, panelWindows, reducer, windowAnchor } from "./state/reducer";
import {
  EXAMPLE_QUESTIONS,
  EXAMPLES_LABEL,
  MAIN_HEADING,
  PURPOSE_LINE,
  SKIP_TO_COMPOSER,
  WORDMARK,
} from "./text";

const DESKTOP_QUERY = "(min-width: 1024px)";

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

function subscribeDesktop(callback: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => {};
  }
  const mq = window.matchMedia(DESKTOP_QUERY);
  if (typeof mq.addEventListener !== "function") return () => {};
  mq.addEventListener("change", callback);
  return () => mq.removeEventListener("change", callback);
}

function getDesktopSnapshot(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(DESKTOP_QUERY).matches;
}

function useIsDesktop(): boolean {
  return useSyncExternalStore(subscribeDesktop, getDesktopSnapshot, () => false);
}

export function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  // Session id and bot-check token live in memory only: no cookies, no storage.
  const sessionId = useRef<string>(newSessionId());
  const nextTurnId = useRef(1);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const [checking, setChecking] = useState<string | null>(null);
  const [botError, setBotError] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [prefill, setPrefill] = useState<QueryPrefill | null>(null);
  const [scrolled, setScrolled] = useState(false);
  const [wordmarkEntering, setWordmarkEntering] = useState(false);

  const isDesktop = useIsDesktop();
  const hasTurns = state.turns.length > 0;
  const prevHasTurns = useRef(hasTurns);

  const last = state.turns[state.turns.length - 1];
  const busy = last?.phase === "streaming";

  useEffect(() => {
    const ctl = new AbortController();
    health(ctl.signal).then((h) => h && dispatch({ type: "health", snapshot: h.snapshot }));
    return () => ctl.abort();
  }, []);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useLayoutEffect(() => {
    if (!prevHasTurns.current && hasTurns) {
      prevHasTurns.current = true;
      setWordmarkEntering(true);
      const id = requestAnimationFrame(() => setWordmarkEntering(false));
      return () => cancelAnimationFrame(id);
    }
    prevHasTurns.current = hasTurns;
    return undefined;
  }, [hasTurns]);

  const executeAsk = useCallback(
    (query: string, turnstileToken: string | null) => {
      const turnId = nextTurnId.current++;
      const req: AskRequest = { query, session_id: sessionId.current };
      if (turnstileToken) req.turnstile_token = turnstileToken;
      dispatch({ type: "start", turnId, query });
      const ctl = new AbortController();
      abort.current = ctl;
      void ask(
        req,
        {
          onEvent(event, raw) {
            dispatch({ type: "event", turnId, event, raw });
          },
          onFailure(failure) {
            dispatch({ type: "failure", turnId, failure });
          },
        },
        ctl.signal,
      ).finally(() => {
        if (abort.current === ctl) abort.current = null;
      });
      requestAnimationFrame(() => {
        document
          .getElementById(`turn-${turnId}`)
          ?.scrollIntoView?.({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
      });
    },
    [],
  );

  const gateRef = useRef<TurnstileGate | null>(null);
  const getGate = useCallback((): TurnstileGate => {
    if (gateRef.current == null) {
      gateRef.current = new TurnstileGate({
        onSend: (query, token) => executeAsk(query, token),
        onReset: () => setResetKey((k) => k + 1),
        onStatusChange: (status) => setChecking(status),
        onError: (err) => setBotError(err),
      });
    }
    return gateRef.current;
  }, [executeAsk]);

  useEffect(() => {
    return () => {
      gateRef.current?.cancel();
      abort.current?.abort();
    };
  }, []);

  const askQuery = useCallback(
    (query: string) => {
      const gate = getGate();
      if (busy || gate.isWaiting()) return;
      setBotError(null);
      gate.ask(query, Boolean(TURNSTILE_SITE_KEY));
    },
    [busy, getGate],
  );

  const fillExample = useCallback((example: string) => {
    setPrefill((prev) => ({ text: example, seq: (prev?.seq ?? 0) + 1 }));
  }, []);

  const showSource = useCallback((turnId: number, passageId: string) => {
    dispatch({ type: "focus", turnId });
    // React commits a click's update before the next frame, so the window exists by then.
    requestAnimationFrame(() => {
      const el = document.getElementById(windowAnchor(turnId, passageId));
      if (!el) return;
      el.scrollIntoView?.({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
      el.focus({ preventScroll: true });
    });
  }, []);

  let blocked: string | null = null;
  if (botError) {
    blocked = botError;
  } else if (checking) {
    blocked = checking;
  }

  const isBlocked = busy || (blocked !== null && blocked !== TURNSTILE_TIMEOUT_MESSAGE);
  const active = state.turns.find((t) => t.id === state.activeTurnId) ?? null;

  const composerDock = (
    <div className="sticky bottom-0 z-20 mt-auto">
      <div className="pointer-events-none h-6 bg-gradient-to-b from-transparent to-background" aria-hidden="true" />
      <div className="bg-background pb-[max(1rem,env(safe-area-inset-bottom))]">
        {TURNSTILE_SITE_KEY && (
          <Turnstile
            resetKey={resetKey}
            onToken={(t) => {
              getGate().onToken(t);
              if (t) setBotError(null);
            }}
            onError={(err) => {
              getGate().onFailure(err);
            }}
            onTimeout={() => {
              getGate().onTimeout();
            }}
          />
        )}
        <QueryBox busy={busy} blocked={blocked} onAsk={askQuery} inputRef={input} prefill={prefill} />
      </div>
    </div>
  );

  return (
    <ToastProvider>
      <TooltipProvider>
        <div className="flex min-h-dvh flex-col bg-background text-foreground">
          <a
            href="#q"
            className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-2 focus:z-50 focus:rounded-lg focus:border focus:border-border focus:bg-surface focus:px-3 focus:py-2 focus:text-ui focus:text-foreground"
          >
            {SKIP_TO_COMPOSER}
          </a>

          {hasTurns && (
            <header
              className={cn(
                "sticky top-0 z-30 border-b bg-background/95 backdrop-blur-xs transition-colors duration-[var(--dur-fast)]",
                scrolled ? "border-border" : "border-transparent",
              )}
            >
              <div className="mx-auto flex h-12 w-full max-w-[728px] items-center px-4 sm:px-6 lg:max-w-[1120px]">
                <p
                  className={cn(
                    "masthead-motion origin-left text-ui font-bold text-foreground",
                    wordmarkEntering ? "translate-y-3 scale-115" : "translate-y-0 scale-100",
                  )}
                >
                  {WORDMARK}
                </p>
              </div>
            </header>
          )}

          <div
            className={cn(
              "mx-auto flex w-full flex-1 flex-col px-4 sm:px-6",
              hasTurns ? "max-w-[728px] py-6 lg:max-w-[1120px]" : "max-w-[728px] pt-[12vh]",
            )}
          >
            {!hasTurns && (
              <header className="mb-8">
                <p className="masthead-motion text-display text-foreground">{WORDMARK}</p>
                <p className="mt-2 text-body text-muted-foreground">{PURPOSE_LINE}</p>
              </header>
            )}

            <div
              className={cn(
                "flex flex-1 flex-col",
                hasTurns && "lg:grid lg:grid-cols-[minmax(0,680px)_360px] lg:items-start lg:gap-8",
              )}
            >
              <main id="main" className="flex min-w-0 flex-1 flex-col">
                <h1 className="sr-only">{MAIN_HEADING}</h1>
                {!hasTurns ? (
                  <ul className="mb-8 flex flex-wrap gap-2" aria-label={EXAMPLES_LABEL}>
                    {EXAMPLE_QUESTIONS.map((q) => (
                      <li key={q} className="max-w-full">
                        <Chip
                          variant="outline"
                          className="h-auto max-w-full whitespace-normal py-2 text-left"
                          onClick={() => fillExample(q)}
                        >
                          {q}
                        </Chip>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="space-y-8 pb-6">
                    {state.turns.map((t) => {
                      const showInlineSources =
                        (!isDesktop && (t.id === active?.id || panelWindows(t).length > 0)) ||
                        (isDesktop && t.id !== active?.id && panelWindows(t).length > 0);
                      return (
                        <div
                          key={t.id}
                          className="space-y-6 border-b border-border pb-8 last:border-b-0 last:pb-0"
                        >
                          <Turn turn={t} busy={isBlocked} onAsk={askQuery} onShowSource={showSource} />
                          {showInlineSources && (
                            <div>
                              <SourcePanel turn={t} />
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
                {composerDock}
              </main>

              {hasTurns && isDesktop && active !== null && (
                <div className="lg:sticky lg:top-16 lg:max-h-[calc(100dvh-5rem)] lg:overflow-y-auto">
                  <SourcePanel turn={active} />
                </div>
              )}
            </div>
          </div>

          <Footer snapshot={state.snapshot} wide={hasTurns} />
        </div>
      </TooltipProvider>
    </ToastProvider>
  );
}
