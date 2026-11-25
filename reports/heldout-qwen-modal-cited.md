# Held-out battery, Qwen judge on Modal, judge on cited windows only: result

Method `reports/heldout-qwen-modal-cited-method.md` (commit 96e6f11), runner at 4b47845 (cold-start fix, see `heldout-qwen-modal-cited-attempt1.md`). Run 25 Nov 2026 05:33 to 05:38 UTC on dpl015, once. 328 judge calls, 0 judge errors, 0 retries, client p50 821 ms, p95 1,108 ms, cited fallback 0, estimated cost 0.34 USD. Raw: `reports/heldout-qwen-raw-modal-cited/`.

| Detector | Caught | False rollback (pass) | False rollback (GP) | Precision |
|---|---|---|---|---|
| Qwen judge on Modal, all candidates (20 Nov) | 99.4% (154/155) | 7.3% (14/191) | 4.5% (8/179) | 91.7% (154/168) |
| Qwen judge on Modal, cited windows only (this run) | 100.0% (155/155), CI [97.6, 100.0] | 12.6% (24/191), CI [8.2, 18.1] | 10.1% (18/179), CI [6.1, 15.4] | 86.6% (155/179) |

Merge rule (pre-registered): caught at least 154/155 AND false rollbacks on pass rows at most 14/191. **Fails** on false rollbacks (24 > 14). The change stays off: production keeps `STRICT_FIGURES` (the judge sees every candidate).

## Where it differs

11 rows changed verdict, all from emit to rollback; none the other way. 10 are grounded paraphrases, 1 a dropped qualifier (vhold-0140, now caught). In every one the premise is a whole section and the excerpt is one subsection: the judge saw the subsection without the rest of its section (vhold-0001: "Turnover Not more than £25.9 million" without the conditions it belongs to). Not stated (8), contradiction (3), strict neutral (1).

## Reading

Cutting the judge's view to the cited window removes context a correct sentence needs. A narrower cut than the section is not safe on this evidence. Any later scope (for example every window of the cited section) is a new detector with its own pre-registered run.
