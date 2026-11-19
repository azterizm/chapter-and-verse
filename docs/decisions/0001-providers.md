# 0001 Providers for Qwen3.8-27B

Checked 2026-11-18 (agent R1, Slice 1). This record covers which providers serve generation and the sentence judge, and with which request settings. Each fact is labelled live (seen in an API call on 2026-11-18), doc (read on the page cited), or measured (from the legal-rag-verifier repo). Where a fact could not be found, the table says not stated and names the place looked.

## Groq

| Fact | Value | Source | Checked |
|---|---|---|---|
| Model id | `qwen/qwen3.8-27b` (not `qwen3.8-27b`); the only Qwen model of 11 listed; status Preview | live `GET /openai/v1/models`; doc console.groq.com/docs/model/qwen/qwen3.8-27b | 2026-11-18 |
| Quantisation | Not disclosed as a bit width; page says "TruePoint Numerics", "reduces precision only in areas that don't affect accuracy" | doc model page | 2026-11-18 |
| Context, max output | 131,072 / 16,384 tokens | live models list | 2026-11-18 |
| Requests per day | 1000 | live header `x-ratelimit-limit-requests` | 2026-11-18 |
| Tokens per minute | 8000 | live header `x-ratelimit-limit-tokens` | 2026-11-18 |
| Requests per minute, tokens per day | 30 and 200K, but the docs table is labelled "base limits for the Developer plan"; the free-plan tab could not be read; not in headers | doc console.groq.com/docs/rate-limits | 2026-11-18 |
| Limit scope | "Rate limits apply at the organization level, not individual users." | doc rate-limits | 2026-11-18 |
| Endpoint | `https://api.groq.com/openai/v1/chat/completions` | doc console.groq.com/docs/openai | 2026-11-18 |
| Accepted | `temperature`, `top_p`, `presence_penalty`, `seed`, `stop`, `max_tokens`, `stream`, `reasoning_effort`, `reasoning_format` | live | 2026-11-18 |
| Rejected (HTTP 400) | `min_p` ("property 'min_p' is unsupported"), `top_k`, `chat_template_kwargs` | live | 2026-11-18 |
| Thinking off | `reasoning_effort: "none"`; omitting it also gave no reasoning (8 tokens); `/no_think` accepted, same output | live; doc console.groq.com/docs/reasoning | 2026-11-18 |
| Thinking on | `reasoning_effort: "medium"`, `reasoning_format: "parsed"` returns `message.reasoning` | live | 2026-11-18 |
| Streaming | Works (SSE deltas) | live | 2026-11-18 |
| Region seen | Response header `x-groq-region: dmm` | live | 2026-11-18 |
| Terms: public app | Allowed through a "Customer Application" used by "End Users"; preamble "not for consumer use" binds the account holder, not end users | doc console.groq.com/docs/legal/services-agreement (last modified 2026-06-22) | 2026-11-18 |
| Terms: legal content | §4.3 "The AI Model Services should not be used for medical, legal, financial, or other professional advice." AUP lists legal as high risk for "automated decisions that have a material detrimental impact on individual rights without human supervision" | services agreement; console.groq.com/docs/legal/ai-policy (effective 2025-10-15) | 2026-11-18 |
| Terms: fees, accounts | §6.3(d)(iii) no use "in a manner intended to avoid incurring Fees"; AUP forbids use "beyond published parameters, rate limits ... including by registering multiple accounts" | services agreement; AI policy | 2026-11-18 |
| Terms: data | §4.2 "Groq does not access, use, store, or retain Inputs or Outputs except as necessary to provide the Cloud Services"; "Groq is not permitted to use Inputs or Outputs for training"; zero retention setting for eligible customers | services agreement | 2026-11-18 |
| Terms: attribution, region | No attribution clause; no region clause beyond export control | services agreement | 2026-11-18 |

## Cloudflare Workers AI

| Fact | Value | Source | Checked |
|---|---|---|---|
| Model id | `@cf/qwen/qwen3.8-27b`, no deprecation date | live `GET /ai/models/search?search=qwen`; doc developers.cloudflare.com/workers-ai/models/qwen3.8-27b/ | 2026-11-18 |
| Quantisation | Not disclosed | doc model page; live model properties | 2026-11-18 |
| Context | 262,144 tokens | live model properties | 2026-11-18 |
| Free allocation | 10,000 Neurons per day, reset 00:00 UTC; on Free, requests past the limit fail | doc developers.cloudflare.com/workers-ai/platform/pricing/ (modified 2026-11-12) | 2026-11-18 |
| Neuron cost | 40,909 per M input, 290,909 per M output ($0.45 / $3.20); cached input $0.05 per M | doc pricing; live price property | 2026-11-18 |
| Neuron cost per call | Returned as `usage.neurons` and header `cf-ai-neurons`; 28 in + 8 out cost 3.47, which matches the table | live | 2026-11-18 |
| Rate limit | "300 requests per minute" for text generation; the model is not named; no daily request limit stated; no rate-limit headers returned | doc developers.cloudflare.com/workers-ai/platform/limits/ | 2026-11-18 |
| Endpoint | `https://api.cloudflare.com/client/v4/accounts/<account>/ai/v1/chat/completions` | doc developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/; live | 2026-11-18 |
| Accepted (HTTP 200) | `temperature`, `top_p`, `top_k`, `min_p`, `presence_penalty`, `seed`, `stop`, `max_tokens`, `stream`, `chat_template_kwargs`, `continue_final_message`, `add_generation_prompt`. A 200 does not prove a field is applied; `top_k`, `min_p`, `continue_final_message`, `add_generation_prompt` are not in the docs list; `max_tokens` is listed as deprecated in favour of `max_completion_tokens` | live; doc model page | 2026-11-18 |
| Thinking off | `chat_template_kwargs: {"enable_thinking": false}` gave `reasoning: null`, 8 tokens. Omitting it turns thinking on (44 tokens, 15.4 Neurons). `reasoning_effort` accepts only low, medium, xhigh (default); no off value | live; live model properties | 2026-11-18 |
| Streaming | Works (SSE deltas) | live | 2026-11-18 |
| Terms: public app | "You acknowledge and agree that you are solely responsible for the acts of your End Users."; "You are solely responsible for all Outputs and Actions." No legal-content or attribution clause | doc cloudflare.com/service-specific-terms-developer-platform/ (updated 2026-09-28) | 2026-11-18 |
| Terms: data | "Cloudflare does not use Customer Content processed by Workers AI to train AI models."; stateless requests: inputs and outputs not retained "after they are no longer needed to process the request"; no region statement | doc developers.cloudflare.com/workers-ai/platform/privacy/ (modified 2026-10-16) | 2026-11-18 |

## Modal (self-hosted SGLang)

| Fact | Value | Source | Checked |
|---|---|---|---|
| Starter credit | "$30 / month free credits"; 10 GPU concurrency | doc modal.com/pricing | 2026-11-18 |
| GPU price | L4 $0.000222/s (about $0.80/h); L40S $0.000542/s (about $1.95/h); CPU $0.0000131 per core-s and memory $0.00000222 per GiB-s on top | doc modal.com/pricing | 2026-11-18 |
| $30 buys | About 15.4 L40S-hours before CPU and memory; about 13.4 h with the verifier ledger's 15% overhead | derived | 2026-11-18 |
| Budget control | Workspace budget, set on the Usage & Billing page in the dashboard by Owners or Managers; no CLI or API stated. It is "the hard outer cap for the entire Workspace" and caps usage "before credits are applied". What happens to a running container at the cap is not stated. Default spend limit is usage limit minus credits, so a $30 budget gives $0 out of pocket | doc modal.com/docs/guide/budgets | 2026-11-18 |
| Scale to zero | Default with `min_containers` 0; `scaledown_window` default 60 s, range 2 s to 20 min; `modal_serve.py` uses 120 s and `max_containers=1` | doc modal.com/docs/guide/cold-start; repo | 2026-11-18 |
| Cold start, measured | 406 s to ready (stop 78, attempt 2, one L40S, weights on a volume). Attempt 1 crash-looped under the 300 s default startup limit; needs `startup_timeout=1800` | measured, legal-rag-verifier docs/GRID.md stop 78 | 2026-11-02 |
| Judge preset | `qwen27-ar4`: `Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ` rev `1b9d56b4`, W4A16, SGLang 0.5.21, `--reasoning-parser qwen3`, 65,536 context. 91.4% (138/151), server p50 0.58 s | measured, docs/measurements.md stop 78 | 2026-11-02 |
| Prefill | Not tested here; SGLang runs the plan's token-level rollback (`InFlightGenerator`) | plan §5 stage 8 | 2026-11-18 |
| Workspace state | Active CLI profile: 0 apps, 0 volumes. The weights volume `product-answer-checks-hf` exists only in another workspace | live `modal app list`, `modal volume list` | 2026-11-18 |

Model licence: Qwen/Qwen3.8-27B is Apache-2.0, no attribution clause (huggingface.co/Qwen/Qwen3.8-27B, 2026-11-18).

## Prefill and thinking-off results (live)

Test: system "Reply in one sentence.", user "Name the capital of France.", final assistant message with partial text. A French prefix was used because a fresh English turn cannot be mistaken for a continuation of it.

| Provider | Request | Reply | Verdict |
|---|---|---|---|
| Groq | prefix "La capitale de la France est" | "Paris" (2 tokens) | True prefill; returns only the continuation, leading space trimmed (also in stream) |
| Groq | prefix "The capital of France is Pa" | "The capital of France is Paris, and ..." (16 tokens) | A prefix that ends mid-word breaks continuation |
| Cloudflare | prefix "La capitale de la France est", default | "The capital of France is Paris." (8 tokens) | New turn; no prefill |
| Cloudflare | same, plus `continue_final_message: true`, `add_generation_prompt: false` | " Paris." (3 tokens, prompt 34 vs 43) | True prefill, leading space kept; undocumented fields |
| Groq | `reasoning_effort: "none"` | 8 tokens, no reasoning | Thinking off |
| Cloudflare | `chat_template_kwargs.enable_thinking: false` | `reasoning: null`, 8 tokens | Thinking off; default is on |

## Decision

1. All three enter the rotation in the planned order: Groq, Cloudflare, Modal. None of the three sets of terms forbids a free public app.
2. Groq request: `model qwen/qwen3.8-27b`, `reasoning_effort "none"` (short mode and judge) or `"medium"` with `reasoning_format "parsed"` (synthesis), `temperature`, `top_p`, `presence_penalty`, `seed`, `stop`, `max_tokens`. Never send `top_k`, `min_p` or `chat_template_kwargs`; the client strips them for Groq.
3. Cloudflare request: `model @cf/qwen/qwen3.8-27b`, always `chat_template_kwargs {"enable_thinking": false}` except in synthesis mode (`true`), full sampling set. For a continuation add `continue_final_message true`, `add_generation_prompt false`.
4. Modal request: the `qwen27-ar4` preset unchanged, on its own SGLang server.
5. Judge sampling everywhere is the measured preset: temperature 0.7, top_p 0.8, top_k 20, min_p 0, presence_penalty 1.5, max_tokens 1024, seed 0, thinking off. On Groq the judge runs without top_k and min_p, and on Groq and Cloudflare with an undisclosed quantisation. The judge stays on Modal until each provider passes the held-out battery (plan §12 stop rule).
6. Generation sampling in short mode is Qwen's non-thinking card (0.7, 0.8, 20, 0, 1.5); in synthesis mode the thinking card (1.0, 0.95, 20, 0, 0).
7. `ChatContinuationGenerator` sends the verified prefix as a final assistant message only on a provider whose native prefill is on. It cuts the prefix at a sentence end only. Native prefill is on by default for Cloudflare and off by default for Groq (Amendment 2026-11-19). A passed prefill canary turns it on for that provider; a failed one turns it off. With native prefill off, the provider gets the verified prefix in the user turn (`fold_prefill`). On Groq a native continuation is joined with one space when it starts with neither whitespace nor punctuation; on Cloudflare the text is used as returned.
8. The daily `/models` check adds one prefill canary per provider (the French test above) and one thinking-off canary. If the model is not listed or the thinking-off canary fails, the provider leaves the rotation. A failed prefill canary does not remove the provider: it only switches that provider to the user-turn prefix (item 7).
9. Quota ledger: Groq counts requests per day (1000) and tokens per minute (8000) from response headers, and holds 30 RPM and 200K TPD as unconfirmed ceilings. Cloudflare sums `usage.neurons` against 9,500 of the 10,000 daily Neurons.
10. The UI states that answers are information about legislation, not legal advice, to meet Groq §4.3. One Groq organisation and one Cloudflare account; no extra accounts to stretch limits (Groq AUP).
11. Modal: during the measured 406 s cold start the user gets the extractive answer, not 1 to 3 min as the plan states. Keep `startup_timeout=1800`, `max_containers=1`, `scaledown_window=120`.

## Amendment 2026-11-19

Checked 2026-11-19 (agent B3, live smoke through the provider clients, `max_tokens` 32). This amends the Groq prefill verdict above and decision items 7 and 8.

| Fact | Value | Source | Checked |
|---|---|---|---|
| Groq prefill | Not a reliable continuation. Of 4 requests with the French prefix "La capitale de la France est", 1 replied "Paris" (2 tokens); 3 replied with a fresh answer ("The capital of France is Paris.", "Paris is the capital of France."), with and without `reasoning_effort: "none"` | live | 2026-11-19 |
| Groq prompt tokens | 48 for each of the 4 prefill requests, against 28 for the same messages without the prefix: 20 more. That fits a closed assistant turn plus a new generation prompt, not a continued turn. The 2 short replies seen on 2026-11-18 may have been fresh short answers | live | 2026-11-19 |
| Cloudflare prompt tokens | 34 with `continue_final_message: true`, `add_generation_prompt: false`, against 28 plain: 6 more; reply " Paris." (continued). Latency 14.3 s for this call | live | 2026-11-19 |

Changes:

1. Groq `supports_prefill` is false by default. Groq gets the verified prefix in the user turn (`fold_prefill`) until its prefill canary passes.
2. The prefill canary passes only when the reply starts with "Paris" and its prompt costs at most 12 tokens more than the plain canary's prompt (`PREFILL_MAX_EXTRA_PROMPT_TOKENS`). The token test does not depend on sampling, so a fresh short "Paris" does not pass.
3. A pass turns native prefill on for that provider; a failure turns it off. Neither removes the provider from the rotation. Model list and thinking-off failures still do.

## Open items for the user

1. In the Modal dashboard, Usage & Billing, set the workspace budget to $30 for the workspace the app will use. Confirm that workspace is on Starter. Confirm or copy the `product-answer-checks-hf` weights volume into it, because the active CLI profile has none.
2. In the Groq console, Settings, Limits, confirm the free-plan RPM and TPD for `qwen/qwen3.8-27b`. Confirm the organisation is on the free plan, and decide whether to enable zero data retention.
3. Accept or reject the reading of Groq §4.3 (legislation information with a disclaimer, not legal advice). If rejected, drop Groq.
4. Note that `qwen/qwen3.8-27b` is a Preview model on Groq and may be withdrawn without notice.
5. Cloudflare's Self-Serve Subscription Agreement was not read in full; only the service-specific terms and the privacy page were.
