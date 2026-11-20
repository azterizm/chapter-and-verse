# Judge reservation: requirements

Context: in the Slice 1 latency gate, Groq served 12 requests and Cloudflare 45. The quota ledger (`engine/chapterverse/providers/ledger.py`) reserves a request's full `max_tokens` as worst-case output. A judge call sends `max_tokens` 1024 (the measured preset) but replies in about 18 tokens. Against Groq's 8,000 tokens a minute, the reservation of about 1,000 prompt plus 1,024 output tokens per judge call lets only about 3 judge calls a minute, so the rotation moves judging to Cloudflare, whose daily Neurons are scarcer.

## R1 Find out what Groq counts

User story: as the operator, I want the ledger to match how Groq actually counts tokens a minute, so that Groq is used to its real limit without 429s.

- R1.1 Measure with at most 10 live Groq requests (free tier) whether Groq's per-minute token count (`x-ratelimit-remaining-tokens`) is reduced by the requested `max_tokens` or only by tokens actually used. Method: read the header from consecutive tiny calls with `max_tokens` 1024 and with `max_tokens` 32, same prompt, a few seconds apart; compare the drops. Use `uv run python` with `httpx` (curl is not allowed). Record each call (time, max_tokens, prompt tokens, completion tokens, remaining-tokens header before and after) in `SCRATCH/judge-reservation/groq-tpm.jsonl`.
- R1.2 If Groq's own rate-limit documentation states the rule, quote it with the URL; the measurement decides when they disagree.

## R2 Reserve what will be counted

- R2.1 IF Groq counts only tokens used THEN a judge call (`purpose="judge"`) SHALL reserve prompt estimate plus a judge output reservation of 64 tokens (a named constant), while still sending `max_tokens` 1024; generation calls keep reserving their `max_tokens`.
- R2.2 IF Groq counts requested `max_tokens` THEN the ledger SHALL keep the worst-case reservation and the worker SHALL report that the fix is not possible without changing the measured preset; do not change the preset.
- R2.3 After a call, the ledger SHALL reconcile the reservation with the actual usage it already records, so an over- or under-estimate does not accumulate.
- R2.4 A 429 from Groq after the change SHALL still map to `rate_limit` and move the call to the next provider, as today.

## R3 Tests

- R3.1 Unit tests: judge reservation uses the constant; generation unchanged; reconcile releases the difference; a burst of judge calls fits as many as the minute window allows by the new rule.

`SCRATCH` = `$CV_SCRATCH`.
