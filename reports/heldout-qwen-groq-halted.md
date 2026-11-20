# Held-out run on Groq: halted attempt

The pre-registered held-out run of the Qwen judge on Groq and Cloudflare (method `reports/heldout-qwen-method.md`, commit a491e8d) was halted on 2026-11-20 for capacity.

## Summary

| Item | Value |
|---|---|
| Method commit | a491e8d |
| Started | 2026-11-19T10:36:48Z |
| Halted | 2026-11-20 |
| Reason | Halted for capacity: live site and run shared the Groq account limit (200,000 tokens a day) |
| Groq calls cached | 47 of 274 |
| Cloudflare calls | 0 |
| Rows scored or viewed | None (0 of 346) |
| Cache status | Kept unopened in the scratch directory |

## Record

The run started on 2026-11-19T10:36:48Z under method commit a491e8d. It was halted on 2026-11-20 because the shared Groq daily quota (200,000 tokens a day) was needed by the live site.

At the halt, 47 of the planned 274 Groq calls had been fetched and cached. No Cloudflare calls were sent. No row was scored and no outcome was viewed (the runner scores only in its final pass).

The cache files in `$CV_SCRATCH/c2a/heldout/` remain unopened and uninspected. The user approved replacing this attempt with a pre-registered run on Modal.
