# Held-out battery: Qwen judge on Modal

## Result

Caught: 154 of 155 (99.4%, 95% CI [96.5%, 100.0%]).
False rollbacks: 14 of 191 on pass rows (7.3%, 95% CI [4.1%, 12.0%]); 8 of 179 on grounded paraphrases (4.5%, 95% CI [1.9%, 8.6%]).
Rollback precision: 154 of 168 (91.7%, 95% CI [86.4%, 95.4%]).

## Method

Method file: `reports/heldout-qwen-modal-method.md` at commit 84e6b28 (sha256 a79377cb2a721b47a5b049c955519af367d1e739929566bd4090a10bc517b339).
Battery: seal `heldout-2026-10-06`, sha256 `1df51b991128c317225d58e340f89532fd9cec149adbfb8bec34d5a6a9bb05b9`.
Detector name: `qwen27-ar4-modal`.
The earlier Groq attempt was halted unscored (`reports/heldout-qwen-groq-halted.md`).

## Comparison

| Detector | Caught | False rollback (pass) | False rollback (GP) | Precision | Source |
|---|---|---|---|---|---|
| Qwen judge on Modal | 99.4% (154/155) | 7.3% (14/191) | 4.5% (8/179) | 91.7% (154/168) | `reports/heldout-qwen-raw-modal/summary.json` |
| Claim check + DeBERTa base + enrichment | 80.0% (124/155) | 7.9% (15/191) | 7.3% (13/179) | 89.2% | `results/heldout-2026-10-06-base.json` |
| Claim check only, no NLI, no judge | 53.6% (83/155) | 5.2% (10/191) | 5.0% (9/179) | 89.3% | `results/heldout-2026-10-06-none.json` |
| Qwen judge on Groq | halted unscored | halted unscored | halted unscored | halted unscored | `reports/heldout-qwen-groq-halted.md` |

## By class

| Class | n | Rolled back | Rate | 95% CI |
|---|---|---|---|---|
| connective | 7 | 5 | 71.4% | [29.0%, 96.3%] |
| dropped_qualifier | 18 | 17 | 94.4% | [72.7%, 99.9%] |
| grounded_paraphrase | 179 | 8 | 4.5% | [1.9%, 8.6%] |
| modal_shift | 16 | 16 | 100.0% | [79.4%, 100.0%] |
| premise_correction | 5 | 1 | 20.0% | [0.5%, 71.6%] |
| unsupported_plausible | 41 | 41 | 100.0% | [91.4%, 100.0%] |
| value_swap | 11 | 11 | 100.0% | [71.5%, 100.0%] |
| version_swap | 8 | 8 | 100.0% | [63.1%, 100.0%] |
| wrong_citation | 9 | 9 | 100.0% | [66.4%, 100.0%] |
| wrong_figure | 37 | 37 | 100.0% | [90.5%, 100.0%] |
| wrong_instrument | 15 | 15 | 100.0% | [78.2%, 100.0%] |

## Weak spots

Connective sentences have 5 of 7 rolled back.
Premise corrections have 1 of 5 rolled back.
Dropped qualifiers have one missed failure out of 18 (17 rolled back).
These classes are small and their intervals are wide.

## Run facts

| Item | Value |
|---|---|
| Judge calls | 328 |
| Errors | 0 |
| Retries (attempts over 1) | 0 |
| Client latency p50 | 1004.26 ms |
| Client latency p95 | 1947.66 ms |
| Prompt tokens | 957393 |
| Completion tokens | 6238 |
| Estimated cost | 0.3279 USD |
| Started | 2026-11-20T06:03:46+00:00 |
| Done | 2026-11-20T06:10:14+00:00 |

Settings sent:
- model: Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ
- temperature: 0.7
- top_p: 0.8
- top_k: 20
- min_p: 0.0
- presence_penalty: 1.5
- max_tokens: 1024
- seed: 0
- chat_template_kwargs: {"enable_thinking": false}

## Limits

One model, one quantisation, one provider.
The battery is synthetic-error style.
This run measures the judge in the verifier check, not live answers.
