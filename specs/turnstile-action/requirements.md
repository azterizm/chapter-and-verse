# Turnstile action and token timing: requirements

Live test on 20 Nov 2026 at https://chapterandverse.memonsystems.com: asking "What is the limit of the compensatory award under s.124 ERA 1996?" returned HTTP 403 from the Worker and the page showed "Verification failed. Try again."

Cause 1 (certain): the Worker requires the Turnstile action `ask` (`TURNSTILE_ACTION = "ask"` in `deploy/worker/wrangler.toml`, checked in `deploy/worker/src/turnstile.ts`), but `web/src/components/Turnstile.tsx` renders the widget without an `action`, so siteverify returns an empty action and every token fails with `action-mismatch`.
Cause 2 (possible): the widget uses `appearance: "interaction-only"`; its token arrives asynchronously, and `web/src/App.tsx` sends the question without a token if none has arrived yet (then the Worker answers `turnstile_required`).

- R1 `Turnstile.tsx` SHALL render the widget with `action: "ask"`. Export the action as a constant shared with tests.
- R2 When the user asks and no token is ready, the page SHALL wait for the next token (up to 15 s) and then send the question with it, showing a short status line "Checking..." meanwhile. If no token arrives in 15 s, show "The bot check did not finish. Reload the page." and do not send.
- R3 After each question the widget is reset for a fresh token, as today.
- R4 Tests (vitest): render options include `action: "ask"`; a question asked before the token arrives is sent once the token arrives, with that token; the 15 s timeout path (fake timers). `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build` in `web` pass.
