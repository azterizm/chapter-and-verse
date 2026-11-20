# Global daily budget: requirements

Free provider quotas are shared by every user. Even with per-IP and per-network limits, many clients together can spend a day's quota early. A ceiling with a reserved share keeps generated answers available to ordinary users all day.

- R1 The engine SHALL count generated answers per UTC day across all users, in Postgres when `DATABASE_URL` is set (table `daily_budget(day date primary key, total int, hosting int)`, idempotent DDL appended to `deploy/schema.sql`), else in memory.
- R2 `GLOBAL_DAILY_ANSWERS` (env, default 400) caps generated answers per day. Requests whose network class (`X-CV-Net-Class` header, trusted only with valid Access headers, as `app.py` does for the client IP) is `hosting` may use at most `HOSTING_SHARE` (env, default 0.2) of it. Missing class counts as `other`.
- R3 Over the cap (or over the hosting share for a hosting request): extractive answer with a `notice` (code `global_budget`, message "Today's generated answers are used up. The provision text is shown instead. It resets at 00:00 UTC."). Add the wording to `engine/chapterverse/templates.py` and `web/src/text.ts`.
- R4 The per-IP daily cap (already built, `DAILY_ASK_CAP`) is checked first; both counters increment only for an answer that made a provider call.
- R5 Tests: under and over the global cap; hosting share; missing class; Postgres SQL builders unit-tested without a database; app-level test with the notice.
- R6 Add the env vars to `deploy/engine.env.example` with one-line comments.
- R7 Salt required: when the per-IP daily cap is active, the engine SHALL refuse to start if `CAP_SALT` is empty or shorter than 32 characters (an unsalted sha256 of an IPv4 address is reversible by brute force). Test it.
- R8 Cleanup: rows of `daily_caps` and `daily_budget` older than 2 days SHALL be deleted by the engine itself (for example once per UTC day, on the first write of the day), not by a commented-out statement. Test the SQL builder.
- R9 `web/test/text.test.ts` hard-codes the number of notice codes (7). Make it check the codes it cares about instead of a count, so adding notices does not break it. `npm run test` in `web` SHALL pass.
