# Held-out battery, Qwen judge on Groq with Cloudflare for oversized prompts: method (pre-registered)

Written 2026-11-19 by agent C2a, before any held-out row was sent to a judge. To be committed before the run. Nothing in this method is changed after the first held-out call; the result is reported whatever it is.

## Battery and seal

| Item | Value |
|---|---|
| Battery | `legal-rag-verifier/batteries/verifier/heldout_draft.jsonl`, read only |
| Rows | 346, every row `split: heldout`, 102 premises |
| Seal | `batteries/verifier/seals/heldout-2026-10-06.json`, committed in `a4d9726` |
| Battery sha256 | `1df51b991128c317225d58e340f89532fd9cec149adbfb8bec34d5a6a9bb05b9` (re-hashed 2026-11-19: matches) |
| Previous runs | `heldout-2026-10-06-none` (claim check only) and `heldout-2026-10-06-base` (DeBERTa base + enrichment), both 2026-10-06 |

The run starts only if the battery's sha256 equals the seal's and the value above, and all 346 rows are held-out (`check_seal` in `eval/judge_gate.py`).

## Detector

| Item | Value |
|---|---|
| Verifier | `legal_rag_verifier` 0.5.0 (installed wheel), `JudgedVerifier(judge, STRICT_FIGURES)` |
| Config | `STRICT_FIGURES`: `neutral_policy="strict"`, `neutral_with_grounded_claim="rollback"`, `ungrounded_figure="judge"` |
| Judge | `Judge(chat, reply="short")`, preset name `qwen27-ar4`, prompt sha256 `SHORT_SHA` (stop 61 short reply), 3 attempts, then label `error` scored not stated |
| Chat | `SizeRouter` (rule below) over two `RotationJudgeChat`s, each on a `ProviderRotation` with one provider in its judge order, each wrapped in `GateChat` (cache, pacing) |
| Detector name | `qwen27-ar4-groq` |

| Provider | Model | Settings sent on every call | Dropped from the measured preset |
|---|---|---|---|
| Groq | `qwen/qwen3.8-27b`, quantisation not disclosed | `temperature` 0.7, `top_p` 0.8, `presence_penalty` 1.5, `max_tokens` 1024, `seed` 0, `reasoning_effort` "none" | `top_k` 20 and `min_p` 0 (HTTP 400 on Groq); `chat_template_kwargs` replaced by `reasoning_effort` "none" |
| Cloudflare | `@cf/qwen/qwen3.8-27b`, quantisation not disclosed, 262,144 context | `top_k` 20, `min_p` 0, `presence_penalty` 1.5, `temperature` 0.7, `top_p` 0.8, `max_tokens` 1024, `seed` 0, `chat_template_kwargs` `{"enable_thinking": false}` | none |

## Routing (option 2, chosen by the user 2026-11-19)

A call goes to Cloudflare when `estimate_tokens(system and user messages) + 1024 > 8000`, otherwise to Groq (`heldout_route` in `eval/judge_gate.py`). `estimate_tokens` is the quota ledger's estimate (characters x 1.5 / 4, rounded up, plus 4 a message); 1024 is the preset's `max_tokens`; 8,000 is Groq's free tokens a minute, the limit our ledger enforces. The rule reads only the call's first prompt, so it is fixed before any call. A retry of a call keeps its first two messages and goes where the call went. No live outcome, failure, wait or retry changes a route. It is the fallback the live app uses for a call Groq cannot take.

Applied offline on 2026-11-19 (prompts built by a judge that sends nothing; no verdict scored): 328 judge calls cover 326 of the 346 rows (20 rows never reach the judge). 274 calls go to Groq and 54 calls, in 53 rows, go to Cloudflare. The 54 call keys are listed at the end. The 53 rows by class: wrong_figure 12/37, value_swap 5/11, wrong_citation 2/9, grounded_paraphrase 26/179, dropped_qualifier 3/18, unsupported_plausible 3/41, modal_shift 2/16, none of connective, premise_correction, version_swap or wrong_instrument. If the live run produces a Cloudflare call that is not in the list, or a listed call goes to Groq, the run stops and the mismatch is reported.

## Premises

Built as in `scripts/run_battery.py`: `premises.build(row, data/, enrichment)` for every row, with the enrichment layer as it stood at the seal commit. The layer in the working tree has grown from 190 to 651 units, so its id is now `gemini-3.8-flash-high@dadff980265af27d`. The 190-unit layer is extracted read only (`git archive a4d9726 enrichment/gemini-3.8-flash-high`) into the scratch directory and gives the sealed id `gemini-3.8-flash-high@edb8ccbbc4fc4862`. Checked 2026-11-19: all 102 premises built from the two layers are equal apart from the id. `premises.py` and `data/MANIFEST.json` are unchanged since `a4d9726`. The run stops if any premise carries another id.

## Procedure

1. In chapter-and-verse: `uv run python -m eval.judge_gate heldout --method-commit <sha>` with `--only groq` or `--only cloudflare` for prefetch passes, then once without `--only` to score. Without `--method-commit` no call is sent.
2. Rows in file order. For each row: `verifier.reset()`, then `verifier.check_sentence(premise, row["sentence"], query=row["query"])`. The reset makes the judge's "answer so far" empty, because each row is one sentence with no answer before it (DeBERTa had no such input). Without it, emitted sentences of earlier rows would leak into later prompts. Rows are therefore independent, so a prefetch pass skips a row that has a call routed to the other provider; skipping changes no prompt.
3. One call at a time. Every successful reply is cached under the sha256 of the provider and the full request body, so no call is paid twice and the scoring pass replays the cache. A 429, an open circuit or a full minute window waits and re-sends. A spent daily budget stops the pass, which resumes on a later UTC day. A server error or timeout is a failed attempt for `Judge`, as a transport error was for `OpenAIChat`.
4. Budgets, as coded in the ledger and the runner. Groq: at most 900 requests a UTC day across this run and the fresh2 gate, pings included; 1,000 requests a day, 8,000 tokens a minute and 200,000 tokens a day (an unconfirmed ceiling) in a rolling 24 h window, seeded from the request logs. Cloudflare: 9,500 Neurons a UTC day across this run and the fresh2 gate. Estimated need: about 538,000 Groq tokens (about 3 to 4 days at 200,000) and about 18,300 Cloudflare Neurons (2 days, after the fresh2 Cloudflare run).
5. Nothing is written in the verifier repo. The once-per-detector markers (`heldout-2026-10-06-qwen27-ar4-groq.started` and `.done`) and all raw output go to the c2a scratch directory; `.done` refuses a second run.

## Counting

Copied from `scripts/run_battery.py`, imported and called, not rewritten: `Outcome(row_id, class, expected, verdict, reasons)` per row and `summarise(outcomes)`.

- Pass classes: `grounded_paraphrase`, `connective`, `premise_correction` (191 rows). Every other class is a planted failure (155 rows).
- Caught (failure recall): failure rows with verdict ROLLBACK, over 155.
- False rollbacks: pass rows with verdict ROLLBACK, over 191; and grounded paraphrases alone, over 179.
- Rollback precision: caught over (caught + false rollbacks).
- By class: rolled back over n, for each of the 11 classes.
- Also recorded: the provider of every call, judge calls, judge errors (label `error`) per provider, attempts over 1, latency p50 and p95 per provider, prompt and completion tokens, Neurons, requests by day, failures by kind.

## Tables reported

1. All 346 rows (the headline): the counts above.
2. By provider: the 293 rows with no call routed to Cloudflare (Groq, including the 20 rows with no judge call), and the 53 rows with a call routed to Cloudflare. A row's two calls, where it has two, are both shown with their providers.
3. Secondary line, fixed here: on the same 293 rows, the Qwen result beside DeBERTa base + enrichment and claim check only, both taken from their sealed per-row verdicts (`results/raw/heldout_draft-base-sealed.jsonl`, `heldout_draft-none-sealed.jsonl`, read only) and counted by the same `summarise`.

## Intervals

Clopper-Pearson exact two-sided 95 % intervals on every rate above, from `clopper_pearson` in `eval/judge_gate.py` (checked against scipy `beta.ppf` to 1e-6).

## Comparison rows

| Detector | Caught | False rollback (pass) | False rollback (GP) | Precision | Source |
|---|---|---|---|---|---|
| Claim check only, no NLI, no judge | 53.6 % (83/155) | 5.2 % (10/191) | 5.0 % (9/179) | 89.3 % | `results/heldout-2026-10-06-none.json` |
| Claim check + DeBERTa base + enrichment | 80.0 % (124/155) | 7.9 % (15/191) | 7.3 % (13/179) | 89.2 % | `results/heldout-2026-10-06-base.json` |
| Qwen judge, Groq and Cloudflare, this run | | | | | |

These rows differ in more than the detector. The DeBERTa runs used verifier commit `138943b` (0.1.0.dev0) with the default config (`entail` 0.70, `contradict` 0.40, `neutral_policy` connective); this run uses 0.5.0 with `STRICT_FIGURES`. The claim check gave identical claims on every held-out text between 0.2.0 and 0.3.0 (verifier stop 75); other changes since 0.1.0.dev0 are not separated out. There is no Qwen run on Modal for this battery.

## Rule

This battery is run once for this detector. The result is reported whatever it is, with no change to prompt, settings, config, premises, routing or counting after the first held-out call.

## The 54 calls routed to Cloudflare

Row id and call key (`prompt_key`: sha256 of the system prompt, a newline and the user prompt, as `Judge` records it).

| Row | Call key |
|---|---|
| vhold-0038 | `8128abcd2cf9f1c1a85b9d643a83b7fb75c0bcdea954b29882da2bc836a6ec63` |
| vhold-0039 | `3530d341a0411256e5061fbf2fb489273967d96971969a08dded36f782173de8` |
| vhold-0040 | `fbb9eb3ea5275370ecace2cd900ef06e339fe64f217966414f0ccd83fe1faf3a` |
| vhold-0041 | `aad619727abbe5da0417a64c7a9590196b847d50850b3de9768ada66e4a6bb42` |
| vhold-0042 | `7d2367ccf2024909cb944c5e564a001771866d15f25ce18dd17e848743eac971` |
| vhold-0074 | `e6d688bf616d45bc57079b54b4f8c20bfe5f20ae69f28ceb9d9545b9e1ea0bc2` |
| vhold-0075 | `44f09d75b7646bb4f664fe68249c49e7a2d524e3c036c66e3001b2f226af5922` |
| vhold-0076 | `0b9f73050efb65596a9a42fc2c10509eee35541b2cdbf3b1a67e396b0b90db5f` |
| vhold-0077 | `bd843be98c837ecae05d8ae32584636b7234c009a35c91c63bb84c573bd92e18` |
| vhold-0078 | `c11f9e16795d2a302ff06737ba54c63e0dc13844f4a93080ceead70d7f9aee7b` |
| vhold-0082 | `a64233e1ed4d03d7400986a0fbe76e6a8f60c496a064203f634b6cebc2bf008d` |
| vhold-0082 | `a56472f7b31bbdef950259a7e5aa3fa756ca2bd4d467e769a523c9e5e174d6a6` |
| vhold-0083 | `a24416a0458b900edc3914cb167f4f62b7211fd301a65485d40d7353281f4320` |
| vhold-0084 | `8f422dd321aa84d16b53d83efc8e34f186efd95b21a6002a831fae95c37ede09` |
| vhold-0085 | `f3ca027d48eaa59651dd0611738a0ed4a9291a2a43374cdd30517997d6506451` |
| vhold-0086 | `950dc914d47a769e960d962c376236762a894404f9f36cbe0c4af8701929bb22` |
| vhold-0143 | `65b2b5380e29adacd9715bf368bce50c5187dd5a6050e89c6396a7b5bb097e8e` |
| vhold-0144 | `a47caa76c888136e4e496958fafbfbf50c63a13cb24079381c56600c8a53b0ca` |
| vhold-0145 | `a9c3ff1552dd47c5b92ac2de6bbd35a2c24e774e244a106e577aaf4dbe2ffede` |
| vhold-0168 | `a9016801c2d3f2c70864a090012ce87bdfb82270505e5d2c653a030805fe32c3` |
| vhold-0169 | `5141893d52b80c23400246493b49b56816ff863cfaf328c9d64a507e7420afef` |
| vhold-0170 | `11630ca1fc9735dda16a0cd7b8e2ffb9921d531aa8feb107aef406e564b76db8` |
| vhold-0171 | `0d61388375282853293b553833eefa8a2d9e349d7292bf65336e7e58576e16f6` |
| vhold-0172 | `c9201ef809768408dea385448ecee30c51eb850e95c51e0d9edacdbc15453cc4` |
| vhold-0183 | `f417a2e4a8cc14f36c79b559eb01fb5df4f1844853c179de3d8e4f0a065b7f7b` |
| vhold-0184 | `e34b4d6affea724dc04b0513449938302eb0ecae4fbbb755b5a615855eb15cd2` |
| vhold-0185 | `319c70bad59ab8f684e54824f4c81f934e66919058955d68f4b1c025abbfc65c` |
| vhold-0186 | `33e89c98b8483bca4d235a0d89c2edff1f50fdec19e2ed27e296bebaf82e55a3` |
| vhold-0201 | `54b61cfaf7a1e0dd77e987bd517cf0079dd5ab1282f9a6aa4b3f824aac0b1d66` |
| vhold-0202 | `01dc89e3263ac133dfb443fb784d5ae50be80c21e34eb8f26e9c2f24bcb2945b` |
| vhold-0203 | `96b98eba567245b61bb4ec01da829008dccc39485e250c3cfe52c2b86a3bcf92` |
| vhold-0207 | `af9ff8e15a684e474ee991d1a666ef9ded07c98d03504460967f2c414150f7b0` |
| vhold-0208 | `1332049ce745cad71e5db2f81c61c95b209c401cf960be5c7677fa112a99e40c` |
| vhold-0209 | `0bcb3a786d10f504112eec52598d573a06a079f429f69aeafbeca8d5fe4971a2` |
| vhold-0210 | `b17def86ca163826b826939bbdb1f9b08d27d78798b3827c8e4eed7bab348892` |
| vhold-0211 | `f96d735c75d83bc522dbcd6bf8dc8271530aa4f76877c007e46bf30269c0e560` |
| vhold-0212 | `a1600cbb2ac9ebc7f922731ae622d3039747e1adfc993d6e8163fa89cd1a2251` |
| vhold-0213 | `e78fa821c47afe767f1d35e11dd8fa7d9ff7c1ee493c2efed28d8a55df849391` |
| vhold-0297 | `9820f021e5e680fe01a39e8a36c35dbb2ba3a17dfde76ae78a03e5611471270f` |
| vhold-0298 | `460c7af06dc97c05402d30d38cb028c9cca89211a6baca11e74a5e716372fffc` |
| vhold-0299 | `0a2e3f1cd77e446929865768968a88f42278f4e60746a567a618ee058859fcbe` |
| vhold-0300 | `54c15b378e56198fbd8a35a9fcf4b05f59ade974d75e95a0e98dded9f8372293` |
| vhold-0301 | `f9ae93003593b4586416e679bfc4654edaaccfc8cf6d798b96615557a054ac43` |
| vhold-0333 | `0cb4a5f3d0d80fd2ab0418dc11c97315af5d89bfc4a3968fc73e00ad8f5745b4` |
| vhold-0334 | `a58851f79d244a7e526fba9987e848279379448ed1c191a0122c40b6251a32da` |
| vhold-0335 | `56bbddadc4061808de4c0dd2f164b94eb5f07319f587375ed42a1b789ff6942c` |
| vhold-0336 | `404504f10abe796a63a75c39c344626317dc8bed5425469f43fb7bb76b259f0d` |
| vhold-0337 | `40e399ddd9c91da059077dc4a45468f07ac5a38b22b4782f626ae882a3de1422` |
| vhold-0338 | `8bb194c48189a9fb5fc405b7e049b6f228aad2751b08f45e3729b00dc16ff9de` |
| vhold-0339 | `760be0dba7cc50629e13790c7f4a32ae4e27d5f2b99cd688bea695f0d92f9b0a` |
| vhold-0340 | `c8cf4f9513a8f72696a561e7cbbc90ebd9b4e33a67b1f18f7cfe13275d8be98c` |
| vhold-0341 | `65e43d0ecd2b5a1697aad74262ca1cdaab890b5bc8f7b77dd778cdc093bf10ee` |
| vhold-0342 | `f548b7c19931dca66d02eba87fd22be6d5dc8fe521b580e1dc6eee60201286d6` |
| vhold-0343 | `56363260a142a929d422c3d40da62eb3f7ae7e0ba826bb4a1e47700b3e652513` |
