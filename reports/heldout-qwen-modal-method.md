# Held-out battery, Qwen judge on Modal: method (pre-registered)

Written 2026-11-20, before any held-out row was sent to Modal. To be committed before the run. Nothing in this method is changed after the first held-out call; the result is reported whatever it is.

The previous pre-registered attempt on Groq (method `reports/heldout-qwen-method.md`, commit a491e8d) was halted on 2026-11-20 for capacity without scoring or viewing any row (`reports/heldout-qwen-groq-halted.md`). This method replaces it with a run on Modal in a separate workspace (`dpl015`).

## Battery and seal

| Item | Value |
|---|---|
| Battery | `legal-rag-verifier/batteries/verifier/heldout_draft.jsonl`, read only |
| Rows | 346, every row `split: heldout`, 102 premises |
| Seal | `batteries/verifier/seals/heldout-2026-10-06.json`, committed in `a4d9726` |
| Battery sha256 | `1df51b991128c317225d58e340f89532fd9cec149adbfb8bec34d5a6a9bb05b9` (re-hashed 2026-11-19: matches) |
| Previous runs | `heldout-2026-10-06-none` (claim check only) and `heldout-2026-10-06-base` (DeBERTa base + enrichment), both 2026-10-06; Groq attempt halted unscored |

The run starts only if the battery's sha256 equals the seal's and the value above, and all 346 rows are held-out (`check_seal` in `eval/judge_gate.py`).

## Detector

| Item | Value |
|---|---|
| Verifier | `legal_rag_verifier` 0.5.0 (installed wheel), `JudgedVerifier(judge, STRICT_FIGURES)` |
| Config | `STRICT_FIGURES`: `neutral_policy="strict"`, `neutral_with_grounded_claim="rollback"`, `ungrounded_figure="judge"` |
| Judge | `Judge(chat, reply="short")`, preset name `qwen27-ar4`, prompt sha256 `SHORT_SHA` (stop 61 short reply), 3 attempts, then label `error` scored not stated |
| Chat | `GateChat` over Modal SGLang client, caching by prompt sha256, dollar cap enforced |
| Detector name | `qwen27-ar4-modal` |

| Provider | Model | Settings sent on every call | Dropped from the measured preset |
|---|---|---|---|
| Modal (`dpl015`) | `Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ` at `1b9d56b428e9f841d7ad566f87660b5f47553c97` | `temperature` 0.7, `top_p` 0.8, `top_k` 20, `min_p` 0, `presence_penalty` 1.5, `max_tokens` 1024, `seed` 0, `chat_template_kwargs` `{"enable_thinking": false}` | none |

Every call goes to the Modal SGLang server deployed from `deploy/modal/serve.py` in workspace `dpl015`, with the measured preset unchanged (all sampling fields sent, thinking off). No routing rule, no overflow provider.

## Routing

No routing rule. Every call goes to Modal.

Applied offline (prompts built by a judge that sends nothing; no verdict scored): 328 judge calls cover 326 of the 346 rows (20 rows never reach the judge). All 328 calls go to Modal.

## Premises

Built as in `scripts/run_battery.py`: `premises.build(row, data/, enrichment)` for every row, with the enrichment layer as it stood at the seal commit. The 190-unit layer is extracted read only (`git archive a4d9726 enrichment/gemini-3.8-flash-high`) into the scratch directory and gives the sealed id `gemini-3.8-flash-high@edb8ccbbc4fc4862`. All 102 premises built from the two layers are equal apart from the id. `premises.py` and `data/MANIFEST.json` are unchanged since `a4d9726`. The run stops if any premise carries another id.

## Procedure

1. In chapter-and-verse: `uv run python -m eval.heldout_modal --method-commit <sha>`. Without `--method-commit` no call is sent.
2. Rows in file order. For each row: `verifier.reset()`, then `verifier.check_sentence(premise, row["sentence"], query=row["query"])`. The reset makes the judge's "answer so far" empty, because each row is one sentence with no answer before it. Rows are independent.
3. One call at a time. Every successful reply is cached under the prompt sha under `$CV_SCRATCH/heldout-modal/`, so no call is paid twice and the scoring pass replays the cache.
4. Budget: Enforces a dollar cap `--max-usd` (default 3.00) using the ledger's Modal cost model.
5. Nothing is written in the verifier repo. The once-per-detector markers (`heldout-2026-10-06-qwen27-ar4-modal.started` and `.done`) and all raw output go to `$CV_SCRATCH/heldout-modal/`; `.done` refuses a second run.

## Counting

Copied from `scripts/run_battery.py`, imported and called, not rewritten: `Outcome(row_id, class, expected, verdict, reasons)` per row and `summarise(outcomes)`.

- Pass classes: `grounded_paraphrase`, `connective`, `premise_correction` (191 rows). Every other class is a planted failure (155 rows).
- Caught (failure recall): failure rows with verdict ROLLBACK, over 155.
- False rollbacks: pass rows with verdict ROLLBACK, over 191; and grounded paraphrases alone, over 179.
- Rollback precision: caught over (caught + false rollbacks).
- By class: rolled back over n, for each of the 11 classes.
- Also recorded: judge calls, judge errors (label `error`), attempts over 1, latency p50 and p95, prompt and completion tokens, estimated USD, requests by day, failures by kind.

## Tables reported

1. All 346 rows (the headline): the counts above.
2. Comparison rows: the Qwen result beside DeBERTa base + enrichment and claim check only, both taken from their sealed per-row verdicts (`results/raw/heldout_draft-base-sealed.jsonl`, `heldout_draft-none-sealed.jsonl`, read only) and counted by the same `summarise`. The table states that the Groq attempt was halted unscored.

## Intervals

Clopper-Pearson exact two-sided 95 % intervals on every rate above, from `clopper_pearson` in `eval/judge_gate.py`.

## Comparison rows

| Detector | Caught | False rollback (pass) | False rollback (GP) | Precision | Source |
|---|---|---|---|---|---|
| Claim check only, no NLI, no judge | 53.6 % (83/155) | 5.2 % (10/191) | 5.0 % (9/179) | 89.3 % | `results/heldout-2026-10-06-none.json` |
| Claim check + DeBERTa base + enrichment | 80.0 % (124/155) | 7.9 % (15/191) | 7.3 % (13/179) | 89.2 % | `results/heldout-2026-10-06-base.json` |
| Qwen judge on Groq | halted unscored | halted unscored | halted unscored | halted unscored | `reports/heldout-qwen-groq-halted.md` |
| Qwen judge on Modal, this run | | | | | |

These rows differ in more than the detector. The DeBERTa runs used verifier commit `138943b` (0.1.0.dev0) with the default config (`entail` 0.70, `contradict` 0.40, `neutral_policy` connective); this run uses 0.5.0 with `STRICT_FIGURES`.

## Rule

This battery is run once for this detector. The result is reported whatever it is, with no change to prompt, settings, config, premises or counting after the first held-out call.
