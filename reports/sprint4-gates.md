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

## Answer level (local engine, real providers Groq and Cloudflare, no database), code 0e3138e

| Row | Run | Result | Time | Answer |
|---|---|---|---|---|
| L01 Limitation Act tort | 1 | pass | 3.6 s | "You generally have six years from the date the cause of action accrued to bring an action founded on tort." [s.2], then the abstention (further sentence rejected, DEONTIC_SHIFT) |
| L07 s.172(1)(a) to (d) quote | 1 | pass | 17 ms | extractive `quote_requested`, all four paragraphs, no model call |
| L09 CRA short-term right to reject | 1 | pass | 10.0 s (backup provider: Groq minute window full after back-to-back runs) | 30 days unless an exception applies [s.22(3)], start of the period [s.22(3)], loss of the right [s.22(1)], earlier-expiry agreements not binding [s.22(2)] |
| L09 | 2 | abstained | 2.5 s | judge rejected a dropped qualifier ("unless subsection (4) applies") |
| L09 | 3 | pass in substance | 1.4 s | when the 30-day period starts [s.22(3)] |

Before Sprint 4 (live, 23 Nov): L01 36.6 s to an abstention with 8 citations on a "not stated" sentence; L07 paraphrase plus the abstention printed twice.

## Not yet run
- Live probes and live legal battery (after deploy).
