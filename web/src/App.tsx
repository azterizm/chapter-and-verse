import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { type AskRequest, ask, health, newSessionId } from "./api/client";
import { Footer } from "./components/Footer";
import { QueryBox } from "./components/QueryBox";
import { SourcePanel } from "./components/SourcePanel";
import { Turn } from "./components/Turn";
import { Turnstile, TURNSTILE_SITE_KEY } from "./components/Turnstile";
import { initialState, reducer, windowAnchor } from "./state/reducer";
import { EXAMPLES } from "./text";

const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  // Session id and bot-check token live in memory only: no cookies, no storage.
  const sessionId = useRef<string>(newSessionId());
  const nextTurnId = useRef(1);
  const abort = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);

  const [token, setToken] = useState<string | null>(null);
  const [verified, setVerified] = useState(TURNSTILE_SITE_KEY === "");
  const [botError, setBotError] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);

  const last = state.turns[state.turns.length - 1];
  const busy = last?.phase === "streaming";

  useEffect(() => {
    const ctl = new AbortController();
    health(ctl.signal).then((h) => h && dispatch({ type: "health", snapshot: h.snapshot }));
    return () => ctl.abort();
  }, []);

  useEffect(() => () => abort.current?.abort(), []);

  const askQuery = useCallback(
    (query: string) => {
      if (busy) return;
      const turnId = nextTurnId.current++;
      const req: AskRequest = { query, session_id: sessionId.current };
      // The bot-check token goes with the first question of the session only.
      if (!verified && token) req.turnstile_token = token;
      dispatch({ type: "start", turnId, query });
      const ctl = new AbortController();
      abort.current = ctl;
      let routed = false;
      void ask(
        req,
        {
          onEvent(event, raw) {
            if (event.type === "route") routed = true;
            dispatch({ type: "event", turnId, event, raw });
          },
          onFailure(failure) {
            dispatch({ type: "failure", turnId, failure });
          },
        },
        ctl.signal,
      ).finally(() => {
        if (abort.current === ctl) abort.current = null;
        if (verified) return;
        // A routed question means the server accepted the token. Otherwise the token is spent
        // (they are single use), so ask the widget for a new one.
        if (routed) setVerified(true);
        else setResetKey((k) => k + 1);
      });
      if (req.turnstile_token) setToken(null);
      requestAnimationFrame(() => {
        document
          .getElementById(`turn-${turnId}`)
          ?.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
      });
    },
    [busy, verified, token],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  const showSource = useCallback((turnId: number, passageId: string) => {
    dispatch({ type: "focus", turnId });
    // React commits a click's update before the next frame, so the window exists by then.
    requestAnimationFrame(() => {
      const el = document.getElementById(windowAnchor(turnId, passageId));
      if (!el) return;
      el.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
      el.focus({ preventScroll: true });
    });
  }, []);

  let blocked: string | null = null;
  if (!verified && !token) blocked = botError ?? "Waiting for the bot check.";

  const active = state.turns.find((t) => t.id === state.activeTurnId) ?? null;

  return (
    <div className="page">
      <a className="skip-link" href="#q">
        Skip to the question box
      </a>
      <header className="masthead">
        <p className="wordmark">Chapter &amp; Verse</p>
        <p className="tagline">UK legislation, answered from the text and cited to the provision. It refuses rather than guesses.</p>
      </header>

      <div className="layout">
        <main className="main" id="main">
          <h1 className="visually-hidden">Ask about UK legislation</h1>
          {state.turns.length === 0 ? (
            <section className="intro" aria-labelledby="examples-h">
              <p>
                Ask about an Act or a section. Every sentence of an answer is checked against the provision text
                before it is shown, and each one cites the provision it rests on.
              </p>
              <h2 id="examples-h" className="intro-h">
                Examples
              </h2>
              <ul className="chips">
                {EXAMPLES.map((q) => (
                  <li key={q}>
                    <button type="button" className="chip" disabled={blocked !== null} onClick={() => askQuery(q)}>
                      {q}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <div className="transcript">
              {state.turns.map((t) => (
                <Turn key={t.id} turn={t} busy={busy} onAsk={askQuery} onShowSource={showSource} />
              ))}
            </div>
          )}

          <QueryBox busy={busy} blocked={blocked} onAsk={askQuery} onStop={stop} inputRef={input} />
          {TURNSTILE_SITE_KEY && !verified && (
            <Turnstile
              resetKey={resetKey}
              onToken={(t) => {
                setToken(t);
                if (t) setBotError(null);
              }}
              onError={setBotError}
            />
          )}
        </main>

        <SourcePanel turn={active} />
      </div>

      <Footer snapshot={state.snapshot} />
    </div>
  );
}
