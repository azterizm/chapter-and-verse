# Held-out battery, Qwen judge on Modal, judge on the cited sections: method (pre-registered)

Written 2026-11-25, before any held-out row was sent to Modal for this detector. Committed before the run. Nothing in this method is changed after the first held-out call; the result is reported whatever it is.

Everything is as in `reports/heldout-qwen-modal-method.md` (commit 84e6b28) and its runner as fixed in 4b47845 (a cold server is waited for, never a failed judge attempt), except the items below. This follows the failed cited-window detector (`reports/heldout-qwen-modal-cited.md`): that result is known, and it is why this scope keeps whole sections.

## What changes

| Item | Value |
|---|---|
| Detector name | `qwen27-ar4-modal-section` (own markers under `$CV_SCRATCH/heldout-modal/section/`) |
| Judge candidates | Only candidates whose first window is in the section of a cited window (engine `cited_nli_scorer(..., scope="section")`, policy `STRICT_FIGURES+SECTION_JUDGE`). Claim checks still run over the whole premise. |
| Cited section of a row | The section (`chapterverse.sections.section_coordinate`) of the row's `excerpt_coordinate`; the kept windows are the premise windows in that section. No match: all candidates, counted as `cited_fallback`. |
| Command | `uv run python -m eval.heldout_modal --judge-scope section --method-commit <sha> --max-usd 3`, after warming the server with one health request |

Applied offline (`--dry`, nothing sent): 328 judge calls, all to Modal; 65 of 346 rows get a judge prompt different from the all-candidates detector (the rest have one-section premises, so the prompt is the same); `cited_fallback` 0; judge prompt characters 3,047,402 (all candidates 3,940,595); estimated cost 0.45 USD. All 328 calls are sent fresh (no reuse of the 20 Nov replies).

## Merge rule (decided now)

The engine change merges only if this run gives BOTH:
- caught at least 154 of 155, and
- false rollbacks on pass rows at most 14 of 191.

Otherwise the scope stays off (`STRICT_FIGURES`) and the result is reported.

## Tables reported

As in the base method, with comparison rows for the all-candidates run (20 Nov) and the cited-window run (25 Nov), and the per-row verdict differences against the all-candidates run.
