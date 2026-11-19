# Per-IP daily cap: requirements

The site is public. Free provider quotas (Groq 1,000 requests a day; Cloudflare 10,000 Neurons a day) are shared by every user. A per-minute limit exists (Worker 10 a minute per IP, engine token bucket). There is no daily limit, so one client can use most of a day's quota.

- R1 The engine SHALL cap generated answers per client per UTC day at `DAILY_ASK_CAP` (env, default 40). Refusals, clarifications and extractive answers that make no provider call do not count.
- R2 The client key SHALL be `sha256(salt + client_ip)`, where the salt is `CAP_SALT` from env (a secret). The raw IP is never stored or logged. The client IP is the one `engine/chapterverse/app.py` already trusts (the `X-CV-Client-IP` header only with valid Access headers, else the socket peer).
- R3 Counts SHALL live in Postgres when `DATABASE_URL` is set (new table `daily_caps(key_hash text, day date, count int, primary key(key_hash, day))`, idempotent DDL appended to `deploy/schema.sql`, rows older than 2 days deleted by the same statement path), and in memory otherwise.
- R4 Over the cap, the answer SHALL be extractive with a `notice` (code `daily_cap`, message "Daily limit reached for generated answers. The provision text is shown instead. It resets at 00:00 UTC."), never an error, never a refusal of the law. Add the notice wording to `engine/chapterverse/templates.py` and to the web notice map in `web/src/text.ts`.
- R5 Tests: under, at and over the cap; refusals not counted; key hashing never stores the IP; Postgres SQL builders unit-tested without a database, like `tests/test_persistence.py`; app-level test that the 41st generated answer is extractive with the notice.
- R6 Add `DAILY_ASK_CAP` and `CAP_SALT` to `deploy/engine.env.example` with one-line comments.
