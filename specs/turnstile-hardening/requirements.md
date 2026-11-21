# Turnstile hardening: requirements

A user saw "Checking..." then "The bot check did not finish. Reload the page." on several questions; later the same flow worked. The widget is rendered in `interaction-only` mode, so it stays invisible unless Cloudflare needs a click, and the page waits 15 s for a token. Console errors about `data:` and CSP come from inside Cloudflare's own Turnstile frame and are harmless; do not try to fix them.

- R1 Wait up to 30 s for a token (`TURNSTILE_TIMEOUT_MS`).
- R2 On timeout, reset the widget once and wait again (another 30 s) before showing the error; never more than one retry per question.
- R3 Render the widget with appearance `always` so the user can see the check running and click it if Cloudflare asks; keep it compact under the question box (size `compact` or `flexible`, whichever fits the layout at phone width with no horizontal scroll).
- R4 Handle Turnstile's `timeout-callback` (reset and retry within R2's single retry) and `unsupported-callback` (message: "This browser cannot run the bot check. Try another browser.").
- R5 Error message after the retry: "The bot check did not finish. Click Ask again, or reload the page if it keeps failing." Clicking Ask again starts a fresh attempt.
- R6 Tests (vitest, fake timers): token arrives late (25 s) and the question is sent; first wait times out, reset, token arrives and the question is sent; both waits time out and the R5 message shows; unsupported callback message; render options include `appearance: "always"` and `action: "ask"`. `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build` pass.
