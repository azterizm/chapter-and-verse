# Held-out report (Qwen judge on Modal): requirements

The pre-registered run (method `reports/heldout-qwen-modal-method.md`, commit 84e6b28, method file sha256 a79377cb2a721b47a5b049c955519af367d1e739929566bd4090a10bc517b339) finished on 2026-11-20. Its raw output is in `reports/heldout-qwen-raw-modal/` (`summary.json`, `raw.jsonl`, `calls.jsonl`, `requests.jsonl`, `.started`, `.done`).

- R1 Write `reports/heldout-qwen-modal.md` from `reports/heldout-qwen-raw-modal/summary.json` only. Every number comes from that file; do not recompute verdicts.
- R2 Sections:
  1. Result in three plain lines (caught, false rollbacks on pass rows and on grounded paraphrases, rollback precision), each with its count, percent and 95% Clopper-Pearson interval as the summary gives them.
  2. Method in at most six lines, pointing to the method file, its commit and sha256, the battery sha256 and the seal, the detector name, and that the earlier Groq attempt was halted unscored (`reports/heldout-qwen-groq-halted.md`).
  3. Comparison table on the same 346 rows: Qwen judge on Modal; claim check + DeBERTa base + enrichment (80.0% caught 124/155, 7.9% false rollback 15/191, 7.3% on grounded paraphrases 13/179, 89.2% precision); claim check only (take its numbers from the method file's comparison table).
  4. By class: a table of n, rolled back, rate and interval for all 11 classes.
  5. Weak spots, stated plainly from the class table: connective sentences (5 of 7 rolled back), premise corrections (1 of 5), the one missed dropped qualifier. Say the classes are small and the intervals wide.
  6. Run facts: 328 judge calls, 0 errors, 0 retries, client latency p50 and p95, prompt and completion tokens, estimated cost, settings sent (from the summary), started and done timestamps.
  7. Limits: one model, one quantisation, one provider; the battery is synthetic-error style; this run measures the judge in the verifier's check, not live answers.
- R3 Voice: cold, plain, short, facts only. No em or en dashes, arrows, curly quotes, or the signs ≥ ≈ ×. No "**Label:** text" lead-ins. Tables are fine.
