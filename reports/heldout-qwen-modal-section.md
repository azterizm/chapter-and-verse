# Held-out battery, Qwen judge on Modal, judge on the cited sections: result

Method `reports/heldout-qwen-modal-section-method.md` (commit eda47fd). Run 25 Nov 2026 06:05 to 06:17 UTC on dpl015, once. 328 judge calls, 0 errors, 0 retries, client p50 1,072 ms, p95 1,966 ms, cited fallback 0, estimated cost 0.51 USD. Raw: `reports/heldout-qwen-raw-modal-section/`.

| Detector | Caught | False rollback (pass) | False rollback (GP) | Precision |
|---|---|---|---|---|
| All candidates (20 Nov) | 99.4% (154/155) | 7.3% (14/191) | 4.5% (8/179) | 91.7% |
| Cited windows (25 Nov) | 100.0% (155/155) | 12.6% (24/191) | 10.1% (18/179) | 86.6% |
| Cited sections (this run) | 100.0% (155/155), CI [97.6, 100.0] | 9.4% (18/191), CI [5.7, 14.5] | 6.7% (12/179), CI [3.5, 11.4] | 89.6% |

Merge rule (pre-registered): caught at least 154/155 AND false rollbacks on pass rows at most 14/191. **Fails** (18 > 14). The scope stays off; production keeps `STRICT_FIGURES`.

## Where it differs from the all-candidates run

Seven rows changed verdict.

- Scope (judge prompt differs from 20 Nov): vhold-0003, 0009, 0011, grounded paraphrases now rolled back (not stated). Their premise is one section, but the all-candidates judge also saw candidates outside the excerpt's section (enrichment and joined windows); without them the paraphrase was not stated.
- Run-to-run variation (judge prompt byte-identical to 20 Nov): on 261 rows with identical prompts, 8 got a different judge label and 4 changed verdict: vhold-0079 and 0331 (grounded paraphrases) now rolled back, 0140 (dropped qualifier) now caught, 0264 (grounded paraphrase) no longer rolled back. The judge on Modal is not deterministic between runs at temperature 0.7 with seed 0.

So, of the 4 extra false rollbacks, 3 come from the scope and 1 (net) from run-to-run variation.

## Lesson for the method

The all-candidates baseline (14/191) is itself one draw: a rerun of the same detector moves by a few rows. A rule "no worse than one earlier run" needs a same-day control run of the baseline detector, or several runs. Any further judge-input change should be pre-registered with that control, and developed on non-sealed data first, so the sealed battery is not looked at again for each variant.
