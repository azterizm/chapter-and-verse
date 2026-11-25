# Sprint 5 clarify ranking: sealed concept test run

## Pre-registration (written before the sealed run)

Date: 25 Nov 2026. Branch `sprint-5`. This section is committed before the sealed test split is scored, and is not edited afterwards.

### Candidate

Discovery for citation-less questions (S5.3): the Slice 3 pipeline (BM25F top 50 fused with dense top 50) with the optional clarify prior on: RRF k 20, dense weight 1.05, demotion of schedules and of repealed, Northern Ireland, Scotland, amending and commencement instruments (unless the question names Northern Ireland or Scotland), and section and instrument priors from the router's citation harvest and non-test router battery rows. Only the order of candidates within the top-50 pool changes; nothing binds. Prior artefact `artefacts/clarify_prior.json`, sha256 `81c1ccabf4197e7b2300a024a9e4cc9e181eb58c6bff06e8790e25afef804758`, router snapshot 2026-09-28. Code at commit 90d5f27.

Choices made on dev only: the 112 concept dev hand rows and the 20-row everyday battery (`batteries/everyday/everyday.jsonl`). Dev, dense on: concept hit@10 0.929 to 0.946, MRR 0.750 to 0.758; everyday hit@1 0.45 to 0.70, MRR 0.600 to 0.758.

### Method

From a clean git worktree at the commit of this pre-registration, two runs, each once:
1. Control: `uv run python -m eval.discovery_gate --split test --sealed-test --dense-dir artefacts/dense/index --models-dir artefacts/models --no-prior`
2. Candidate: the same with `--prior artefacts/clarify_prior.json`

No change to code, parameters, prior or index between this commit and the runs. Neither run is repeated.

### Gate (all must hold)

1. Accuracy, hand rows (91), dense on: candidate hit@10 and MRR each at least the control's, and at least the Slice 3 sealed figures (hit@10 0.912, MRR 0.695).
2. Safety identical to the control: phantom candidates 0, route status mismatches none, user probe rows refused 12 of 12, appendix B confident share equal to the control's.

If either fails, the prior stays off in production (`CLARIFY_PRIOR_PATH` unset) and the result is reported here.

## Results

Run 25 Nov 2026 from a clean git worktree at the pre-registration commit 908d27a, each run once. The gate needs a value for `--sealed-test` (a reference file shown side by side, not the seal; the seal is checked separately): both runs used `seals/concept-2026-09-29.json`. A first launch of both runs failed on argument parsing before any row was scored (the flag without a value; then the candidate's arguments passed as one word); the runs below are the only scored ones. Concept seal checked by the gate.

### Gate 1, accuracy (hand rows, 91, dense on)

| Pipeline | hit@1 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| Slice 3 sealed (23 Nov) | 0.593 | 0.813 | 0.912 | 0.695 |
| Control, prior off (this commit) | 0.604 | 0.802 | 0.912 | 0.698 |
| Candidate, prior on | 0.626 | 0.824 | 0.934 | 0.719 |

Without dense, hand rows: control hit@10 0.879, MRR 0.623; candidate 0.879, 0.645.

Candidate at least the control and the Slice 3 figures on hit@10 and MRR: **pass**.

### Gate 2, safety

| Check | Control | Candidate |
|---|---|---|
| Phantom candidates | 0 | 0 |
| Route status mismatches | none | none |
| User probe rows refused (fictional instruments) | 12 of 12 | 12 of 12 |
| Appendix B negatives, confident share | 0.30 | 0.30 |
| Rows whose confident flag, route status or mode changed | | 0 of 153 |

**Pass.**

## Decision

Both gates pass. The clarify prior may be turned on in production by shipping `clarify_prior.json` (sha256 81c1cc...) and setting `CLARIFY_PRIOR_PATH`; the prior is refused automatically if the router snapshot changes.
