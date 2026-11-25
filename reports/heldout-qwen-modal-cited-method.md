# Held-out battery, Qwen judge on Modal, judge on cited windows: method (pre-registered)

Written 2026-11-25, before any held-out row was sent to Modal for this detector. Committed before the run. Nothing in this method is changed after the first held-out call; the result is reported whatever it is.

Everything is as in `reports/heldout-qwen-modal-method.md` (commit 84e6b28; result `reports/heldout-qwen-modal.md`), except the items below.

## What changes

| Item | Value |
|---|---|
| Detector name | `qwen27-ar4-modal-cited` (its own once-per-detector markers under `$CV_SCRATCH/heldout-modal/cited/`) |
| Judge candidates | Only the candidates whose first window is a cited window (engine `chapterverse.audit.verify.cited_nli_scorer`, S5.1b). Claim checks (figures, citations, deontics, as-at) still run over the whole premise. |
| Cited windows of a row | The premise windows whose coordinate equals the row's `excerpt_coordinate`, or is an ancestor or a descendant of it. If no window matches, the row is judged on all candidates and counted as `cited_fallback`. |
| Command | `uv run python -m eval.heldout_modal --judge-scope cited --method-commit <sha> --max-usd 3` |

Applied offline (`--dry`, nothing sent): 328 judge calls over 326 of the 346 rows, all to Modal; `cited_fallback` 0; judge prompt characters 931,817 (3,940,595 with all candidates); estimated cost 0.45 USD.

## Why the rows' excerpt is the citation

In the engine, the judge only runs on a sentence that passed the reference rules, so the sentence names the passages it rests on. The held-out row's `excerpt_coordinate` is the provision the planted sentence is written against (for a pass row its support, for a failure row the provision it distorts), which is what a generator citing honestly would cite. A sentence citing the wrong passage is outside this battery; with cited-only judging it is more likely to be rolled back, not less.

fresh2 (the 151-unit judge gate) has no excerpt coordinate, so it is not run for this detector; any rule picked now would be chosen after seeing the data.

## Merge rule (decided now)

The engine change merges only if this run gives BOTH:
- caught at least 154 of 155 (the all-candidates Modal run: 154/155), and
- false rollbacks on pass rows at most 14 of 191 (all-candidates run: 14/191).

Otherwise the change stays off (policy name `STRICT_FIGURES`) and the result is reported.

## Tables reported

As in the base method, with one more comparison row: Qwen judge on Modal, all candidates, from `reports/heldout-qwen-raw-modal/summary.json`.
