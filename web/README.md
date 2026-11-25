# Chapter & Verse web

The frontend. One page: a question box, the answer as it streams, the source panel, and the footer. Vite, React 18 and TypeScript. No UI kit, no analytics, no cookies, no storage, no external fonts.

## Run

Needs Node 22.12 or later.

```
npm ci
npm run dev          # http://localhost:5173, answers come from the mock
npm run dev:engine   # http://localhost:5173, /api proxied to CV_ENGINE (default http://127.0.0.1:8000), mock and Turnstile off
npm run build        # typecheck, then dist/
npm run preview      # serve dist/ with the production CSP and the mock
npm run typecheck
npm run test
npm run lint
```

To run the local engine for `npm run dev:engine` (bound to `127.0.0.1` only, with `DATABASE_URL` and Modal settings removed):

```
uv run python scripts/dev_engine.py
```

## Environment

| Variable | Use | Default |
|---|---|---|
| `VITE_API_BASE` | API origin, such as `https://api.example.org`. Setting it turns the mock off | Same origin |
| `VITE_TURNSTILE_SITE_KEY` | Cloudflare Turnstile site key. Unset: no widget, no token | Unset |
| `CV_MOCK` | `0` turns the mock off without setting an API origin | On |
| `CV_ENGINE` | Upstream engine URL for Vite `/api` proxy when `CV_MOCK=0` | `http://127.0.0.1:8000` |

A backend on another origin must allow this origin in `CORS_ORIGINS`.

The production build uses `VITE_TURNSTILE_SITE_KEY=0x4AAAAAAGJBUNjQv7_0zIKi` and same-origin API (no `VITE_API_BASE`).

## API

- `POST /api/ask` with `{query, as_at?, session_id?, turnstile_token?}`. The reply is `text/event-stream`, events as in `docs/ARCHITECTURE.md` §4.10, in the order of §5.
- `GET /api/health` returns `{status, corpus_id, snapshot}`. The footer shows the snapshot until a route event gives `index_snapshot`.

The session id is random, made on page load and kept in memory. The Turnstile token goes with the first question of the session. If that question is not routed, the widget is reset for a new token.

## Stream checks

`src/api/sse.ts` reads the body with `fetch`, because EventSource cannot POST. It parses bytes split anywhere: inside an event, inside CRLF, inside a UTF-8 sequence. It skips comment lines. Each event is checked against the §5 order, a port of `contracts.validate_order`, and against the payload shape in `src/api/events.ts`.

On a fault the reader cancels the body and stops. The turn keeps what had already arrived, and an error card states the fault, for example `Got "sentence" after "route". Expected "notice", "clarify", "refusal", "sources", "error".` A stream that ends before `done` is a fault too. No receipt is shown for a failed turn.

The receipt download is the `fact` JSON cut byte for byte from the event's data line, so its SHA-256 equals `answer_id`. The page checks this with Web Crypto and says so.

## Mock

`mock/plugin.ts` serves `/api/ask` and `/api/health` in `dev` and `preview`. It replays the streams in `mock/streams/` with delays of 60 ms to 1.1 s per event. Each event is cut into pieces, one cut inside a multi-byte character, with keep-alive comments between some events.

| Query contains | Stream |
|---|---|
| `marchwood` | `refused.sse`: no such instrument, with a suggestion |
| `429` or `extractive` | `extractive.sse`: every provider failed, provision text with highlights |
| `invalid direction`, `1980/15` or `title only` | `text-not-held.sse`: title-only Act, with the legislation.gov.uk link |
| `violation` | `broken.sse`: out of order on purpose, to show the fault state |
| `124` but not `1996` | `clarify.sse`: two candidates |
| a coordinate outside `uk/ukpga/` | `out-of-coverage.sse` |
| more than 4096 bytes | `too-long.sse`: `error`, `done` |
| anything else | `answer.sse`: ERA 1996 s.124, three cited sentences |

The streams are written by `mock/record.py` with the engine's own dataclasses and `encode_sse`, and checked with `validate_order`. Window text comes from `Premise.from_records` over the test fixture corpus. Provider names, hashes and timings are illustrative. To rewrite them after a contract change, run this from `web/`:

```
npm run mock:record
npm run mock:record:engine
```

`mock:record:engine` (`mock/record_engine.py`) records real-engine SSE streams into `mock/streams/engine/` (using Groq and Cloudflare only, with `DATABASE_URL` and Modal settings removed). When `mock/streams/engine/index.json` exists, `mock/plugin.ts` replays those recorded streams for matching questions and keywords, falling back to `era-124` for unmatched questions and keeping the synthetic error streams unchanged.

`record.py` also writes `order-cases.json`: 400 event sequences with the Python validator's verdict on each. The TypeScript validator is tested against them.

## CSP

The build has no inline script or style. It runs under:

```
default-src 'self' https://challenges.cloudflare.com <API origin>; base-uri 'none'; object-src 'none'; form-action 'self'; frame-ancestors 'none'
```

`npm run preview` sends that header, so a violation shows in the browser console. ESLint rejects `style=` and `dangerouslySetInnerHTML` in JSX.

## Layout

- `src/api/events.ts`: contract types and payload checks.
- `src/api/sse.ts`: parser, order validator and reader.
- `src/api/client.ts`: `ask`, `health` and the receipt bytes.
- `src/state/reducer.ts`: one turn per question, filled event by event.
- `src/components/`: `QueryBox`, `Turn` (notices, clarify, refusal, extractive, sentences, error, receipt, timings), `Citation`, `SourcePanel`, `StatusBadge`, `RefusalCard`, `Receipt`, `Footer`, `Turnstile`.
- `src/text.ts`: user-facing wording.
- `src/styles.css`: light and dark themes from `prefers-color-scheme`.

Every status badge shows its words, with its own colour and border style.
