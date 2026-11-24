# Sprint 4 gates

Code: `sprint-4` at cce3e77, run from a separate worktree on 24 Nov 2026 (real store, router index, dense index `artefacts/dense/index`, bge-small).

## Legal battery (retrieval level, `eval.legal_gate`)

| Split | Before (main 5936f71) | After (cce3e77) |
|---|---|---|
| dev, rows passed | 12/20 | 20/20 |
| dev, must_reach recall | 0.565 (13/23) | 1.000 (23/23) |
| dev, should_reach recall | 0.100 | 0.500 |
| test (sealed, run once), rows passed | not run | 20/20 |
| test, must_reach recall | not run | 1.000 (20/20) |
| test, should_reach recall | not run | 0.75 (3/4) |

Seal: `batteries/legal/SEAL.json` sha256 0bfaa91b... The test split was run once, after all retrieval changes were committed.

## Unchanged safety and discovery

| Gate | Result |
|---|---|
| Router battery, 2,112 rows, through the pipeline | wrong instrument 0, bound on invented 0, pipeline faults 0. Bound changes only on 4 rows with a range citation (uk-misroute-0251, 0265, 0461, 0567), as intended (F10) |
| Concept battery dev (discovery) | hand hit@10 0.929, MRR 0.750 (identical) |
| Security probes, local | 29/29 |

## Not yet run
- Answer level of the legal battery (needs provider calls; off-peak).
- Live probes and live legal battery (after deploy).
