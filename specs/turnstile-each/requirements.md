# Turnstile on every question: requirements

Today the Worker (`deploy/worker/`) verifies Turnstile only on the first question of a session and then trusts a session cookie or id for `SESSION_TTL_SECONDS` (7,200). A script that passes once can keep asking for two hours, limited only by 10 questions a minute per IP. The site is public; free provider quotas are shared by all users.

- R1 Every `POST /api/ask` SHALL carry a fresh Turnstile token, verified by the Worker with Cloudflare's siteverify (hostname and action checked as today). A token is single use; a reused or missing token gets HTTP 403 before anything is forwarded.
- R2 The web page (`web/`) SHALL obtain a new token for each question from the Turnstile widget (re-execute or reset it after each submit), and SHALL show a plain message if verification fails ("Verification failed. Try again.").
- R3 The session id stays for conversation state only; it no longer skips Turnstile.
- R4 The widget SHALL stay in managed (mostly invisible) mode; no change to the Turnstile widget on Cloudflare.
- R5 Tests: Worker vitest cases for missing, invalid, reused and valid tokens on consecutive questions; web vitest for a new token per question. `npx tsc --noEmit` and `npx vitest run` in `deploy/worker`; `npm run typecheck`, `npm run test`, `npm run build` in `web`. All pass.
