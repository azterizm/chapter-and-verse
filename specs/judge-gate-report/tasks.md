# Judge gate report: tasks

Mark each task `[x]` in this file when done.

- [x] 1. Read `requirements.md` and the reference summary `~/Code/legal-rag-verifier/results/fresh2-live78/summary.json`. Derive the reference's 67 wrong and 84 right units from its cells (correct = right minus false rollbacks plus caught) and state the derivation in one line. (R1.2)
- [x] 2. Read the Groq and Cloudflare `summary.json`, `calls.jsonl` and `requests.jsonl`; extract every number in R1.2 to R1.5. (R1.2 to R1.5)
- [x] 3. Compute the Clopper-Pearson 95% intervals in a scratch script; record the script path. (R1.9) - Script: `$CV_SCRATCH/judge-report/compute_intervals.py`
- [x] 4. Write `reports/judge-gate-2026-11.md`. (R1.1 to R1.8, R1.10)
- [x] 5. Check the file for the banned characters with `grep -nP "[\x{2013}\x{2014}\x{2192}\x{2190}\x{201C}\x{201D}\x{2018}\x{2019}\x{2265}\x{2264}\x{2248}\x{00D7}]" reports/judge-gate-2026-11.md` (no output expected). Print a final summary of under 150 words: per provider correct, caught, false rollbacks, verdict.
