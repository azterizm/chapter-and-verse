# Turnstile on every question: tasks

Files you own: `deploy/worker/src/`, `deploy/worker/test/`, `web/src/` except `web/src/text.ts` (another worker edits it; put any new wording in a new file such as `web/src/turnstileText.ts`), `web/test/`. Edit nothing else. Never run git, wrangler, or any deploy.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, and the Worker and web code that handles Turnstile and sessions.
- [x] 2. R1, R3 in the Worker.
- [x] 3. R2 in the web page.
- [x] 4. R5 tests and checks. Print the summary.
