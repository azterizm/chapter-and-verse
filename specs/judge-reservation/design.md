# Judge reservation: design

## Files you own

| File | Change |
|---|---|
| `engine/chapterverse/providers/ledger.py` | The reservation rule and the named constant `JUDGE_OUTPUT_RESERVE` |
| `engine/chapterverse/providers/rotation.py` | Only where the reservation's `max_output_tokens` is computed from the request, if that happens here: pass the judge reserve for `purpose="judge"` |
| `tests/test_providers_ledger.py`, `tests/test_providers_rotation.py` | New tests (R3) |
| `SCRATCH/judge-reservation/measure_tpm.py` | The R1 measurement script (not in the repo) |

Read first: `providers/ledger.py`, `providers/rotation.py`, `providers/openai_compat.py` (how headers are read), `docs/decisions/0001-providers.md`.

Do not edit `audit/` files: another worker is editing them. Do not change `providers/presets.py` (the judge preset stays measured).

The measurement script calls `https://api.groq.com/openai/v1/chat/completions` with model `qwen/qwen3.8-27b`, `reasoning_effort` "none", a one-line prompt, and the `Authorization: Bearer` header read from `GROQ_API_KEY` inside the script. It prints only the numbers, never the key.
