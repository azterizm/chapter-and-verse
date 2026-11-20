# Window wait: requirements

Context: in the live latency run of 19 Nov (`$CV_SCRATCH/gates3/`), 9 of 20 questions got an extractive answer with reason `providers_unavailable`. The ask log shows `failures: {"groq:quota": 1, "cloudflare:quota": 1}`: Cloudflare's daily Neurons were spent, and Groq's local ledger refused because its 8,000 tokens a minute window was still full from the previous question (10 s earlier). Groq would have accepted the request a few seconds later. An answer that waits 3 seconds is better than a quote.

## R1 Wait briefly for a per-minute window

- R1.1 WHEN the ledger refuses a provider only because a per-minute window (tokens or requests a minute) is full AND that window frees enough capacity within `WINDOW_WAIT_MAX_S` seconds (a named constant, default 8) THEN the rotation SHALL wait until then and try that provider again, before trying the next provider.
- R1.2 A daily or monthly quota refusal SHALL never wait; it falls through at once, as today.
- R1.3 The total time one request spends waiting across all its calls SHALL be capped by `REQUEST_WAIT_BUDGET_S` (default 20). Past the cap, refusals fall through at once.
- R1.4 The ledger SHALL expose, for a refusal, the kind of window that refused and the seconds until it frees enough for the requested reservation (`QuotaLedger` Protocol change; update `InMemoryLedger` and the Postgres ledger in `engine/chapterverse/persistence.py` to match).
- R1.5 Waiting SHALL use an injectable sleep and clock, so tests do not sleep.
- R1.6 Each wait SHALL be logged once with provider, window kind and seconds (no query text).

## R2 Tests

- R2.1 Per-minute refusal that frees in 3 s: the rotation waits 3 s (fake clock) and the provider serves.
- R2.2 Frees in 30 s: no wait, falls through.
- R2.3 Daily refusal: no wait.
- R2.4 Budget: several waits in one request stop at `REQUEST_WAIT_BUDGET_S`.
- R2.5 Postgres ledger: the new method on the SQL builders, unit-tested without a database, like the existing persistence tests.
