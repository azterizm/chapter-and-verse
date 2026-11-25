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
