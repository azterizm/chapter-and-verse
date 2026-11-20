# Judge gate report: fresh2 live check (2026-11)

## Method

Evaluates the sentence judge on fresh2 (151 graded units) reproducing `scripts/live_check.py` (stop 78).
Uses `JudgedVerifier` with `STRICT_FIGURES` and the `qwen27-ar4` short prompt.
Only the chat endpoint is swapped for `RotationJudgeChat` pinned to one provider.
Modal SGLang at stop 78 provides the reference baseline.

## Gate results

| Provider | Correct (of 151) | Caught (of 67, 95% CI) | False rollbacks (of 84, 95% CI) | Judge call errors | Client latency p50 / p95 | Prompt / completion tokens | Neurons (Cloudflare) |
|---|---|---|---|---|---|---|---|
| Modal reference | 138 (91.4%) | 62 (92.5%, [83.4%, 97.5%]) | 8 (9.5%, [4.2%, 17.9%]) | 0 | 1.28 s / 1.63 s | not recorded / 2,647 | n/a |
| Groq | 137 (90.7%) | 61 (91.0%, [81.5%, 96.6%]) | 8 (9.5%, [4.2%, 17.9%]) | 0 | 0.19 s / 0.53 s | 112,852 / 2,631 | n/a |
| Cloudflare | 137 (90.7%) | 61 (91.0%, [81.5%, 96.6%]) | 8 (9.5%, [4.2%, 17.9%]) | 0 | 0.96 s / 19.90 s | 112,852 / 2,616 | 5,377.69 |

Derivation of ground truth splits: correct = right - false rollbacks + caught and right + wrong = 151 give right = 138 + 8 - 62 = 84 and wrong = 151 - 84 = 67.
Exact two-sided 95% Clopper-Pearson intervals computed via bisection on the binomial CDF in scratch script `compute_intervals.py`.

## Rules A1, A2, A3

| Provider | A1 accuracy (threshold: 132 to 140 of 151) | A2 validity (threshold: < 5% errors) | A3 latency (threshold: server-side p50 <= 0.81 s) |
|---|---|---|---|
| Modal reference | Pass: 138 of 151 (91.4%) | Pass: 0 / 141 error calls (0.0%) | Pass: server-side p50 0.5761 s (client p50 1.2841 s, client p95 1.6307 s, models p50 0.7080 s) |
| Groq | Pass: 137 of 151 (90.7%) | Pass: 0 / 141 error calls (0.0%) | Pass on client time: client p50 0.1880 s, client p95 0.5258 s. The server-side estimate (client p50 minus the `/models` ping p50, 0.3581 s) is negative and not meaningful on Groq, because the ping is slower than the judge call |
| Cloudflare | Pass: 137 of 151 (90.7%) | Pass: 0 / 141 error calls (0.0%) | Pass: server-side p50 0.7066 s (client p50 0.9566 s, client p95 19.9037 s, models p50 0.2500 s) |

## Agreement with Modal labels

Agreement per call from the summaries:

| Provider | Matched calls | Same label | Different label |
|---|---|---|---|
| Modal reference | 141 | 132 | 9 |
| Groq | 141 | 136 | 5 |
| Cloudflare | 141 | 134 | 7 |

For unique prompts (140 unique prompts), Modal reference has 131 same and 9 different, Groq has 135 same and 5 different, and Cloudflare has 133 same and 7 different.

## Request settings

The measured preset `qwen27-ar4` specifies `Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ` with `temperature` 0.7, `top_p` 0.8, `top_k` 20, `min_p` 0.0, `presence_penalty` 1.5, `max_tokens` 1024, and `seed` 0.

Differences from measured preset:
- Groq drops `top_k` 20 and `min_p` 0, and uses `reasoning_effort` "none" instead of `chat_template_kwargs`.
- Cloudflare retains `top_k` 20 and `min_p` 0.0, and sets `chat_template_kwargs` to `{"enable_thinking": false}`.
- Neither provider discloses its quantisation.

## Verdict

- Modal reference: Pass (138 of 151 correct, inside the 132 to 140 band).
- Groq: Pass (137 of 151 correct, inside the 132 to 140 band).
- Cloudflare: Pass (137 of 151 correct, inside the 132 to 140 band).

## Reference correction

The plan's earlier 80.0% / 7.9% figure is the DeBERTa detector (claim check + base NLI + enrichment) on the sealed held-out set, not a Qwen result; the Qwen reference is stop 78 on fresh2.

A sealed held-out run of the Qwen judge (method pre-registered in `reports/heldout-qwen-method.md`, commit a491e8d) is in progress and reported separately.
