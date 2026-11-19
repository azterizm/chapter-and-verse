# Judge gate report (fresh2 like-for-like): requirements

Context: the sentence judge is Qwen3.8-27B. Its reference measurement is the verifier's stop 78 live check on Modal SGLang over fresh2 (151 graded units): 138/151 correct (91.4%), 62 caught, 8 false rollbacks, 141 calls, 0 errors (`~/Code/legal-rag-verifier/results/fresh2-live78/summary.json`). The same method was re-run through our provider layer (`eval/judge_gate.py run`) on Groq and on Cloudflare. Their outputs are in `SCRATCH/c2a/groq/summary.json` and `SCRATCH/c2a/cloudflare/summary.json` (plus `calls.jsonl`, `requests.jsonl`), and the run logs `SCRATCH/c2a/groq-run.log`, `SCRATCH/c2a/cloudflare-run.log`.

`SCRATCH` = `/private/tmp/claude-501/-Users-abdiel-Code-chapter-and-verse/ff48a7a7-4145-4602-b5a5-4fbe1851fa50/scratchpad`.

## R1 Report

User story: as the operator, I want one dated report of the judge gate per provider, so that the stop rule in plan §3 is decided on recorded numbers.

- R1.1 `reports/judge-gate-2026-11.md` SHALL give the method in at most six lines: the verifier script reproduced (`scripts/live_check.py`, stop 78), the 151 fresh2 units, `JudgedVerifier` with `STRICT_FIGURES`, the `qwen27-ar4` short prompt, only the chat endpoint swapped for `RotationJudgeChat` pinned to one provider.
- R1.2 It SHALL give one table with a row per provider (Modal reference, Groq, Cloudflare) and columns: correct of 151 with percent; caught of 67 with percent and Clopper-Pearson 95% interval; false rollbacks of 84 with percent and Clopper-Pearson 95% interval; judge call errors; client latency p50 and p95; prompt and completion tokens; Neurons (Cloudflare).
- R1.3 It SHALL give the A1, A2, A3 rule results per provider as in the summaries, with their thresholds.
- R1.4 It SHALL list the request settings that differ from the measured preset: Groq drops `top_k` 20 and `min_p` 0 and uses `reasoning_effort` "none"; neither provider discloses its quantisation.
- R1.5 It SHALL give the agreement with the Modal labels per call (matched, same label, different label) from the summaries.
- R1.6 It SHALL give one verdict line per provider against the plan §3 stop rule: the run is a pass when A1 passes (inside the Modal run's band, 132 to 140 of 151).
- R1.7 It SHALL include a "Reference correction" section: the plan's earlier 80.0% / 7.9% figure is the DeBERTa detector (claim check + base NLI + enrichment) on the sealed held-out set, not a Qwen result; the Qwen reference is stop 78 on fresh2.
- R1.8 It SHALL note that a sealed held-out run of the Qwen judge (method pre-registered in `reports/heldout-qwen-method.md`, commit a491e8d) is in progress and reported separately.
- R1.9 Every number SHALL come from the files named above; recompute Clopper-Pearson intervals with `scipy` if installed, else with an exact stdlib implementation (beta quantiles via bisection on the binomial CDF). Show the computation code in the worker's scratch dir, not in the repo.
- R1.10 Voice: cold, plain, short, facts only. No em or en dashes, arrows, curly quotes, or the signs ≥ ≈ ×. No "**Label:** text" lead-ins. Tables are fine.

## Files

The worker creates only `reports/judge-gate-2026-11.md`. Scratch work goes in `SCRATCH/judge-report/`. No git, no network, no provider calls.
