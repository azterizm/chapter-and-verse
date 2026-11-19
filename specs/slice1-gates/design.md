# Slice 1 gates: design

## Files (the worker owns only these)

| File | Role |
|---|---|
| `eval/run.py` | Gate 1 runner, already written and run. Do not change its behaviour. Fix only lint, format or type errors if the checks fail on it |
| `eval/latency.py` | Gate 2 runner (R1, R3) |
| `eval/smoke.py` | Smoke script (R2) |
| `batteries/slice1_questions.jsonl` | The 20 questions (R1.1) |
| `tests/test_eval_smoke.py` | Offline tests for the smoke checks (R2.4) |
| `reports/slice1-gates.md` | The report (R4) |

Do not edit any other file. If a gate exposes an engine bug, record it in the report under "Findings" with the question id and the event transcript path; do not fix engine code.

## How to run things

- Python: `uv run python -m eval.latency ...`, `uv run python -m eval.smoke --base-url http://127.0.0.1:8765`.
- Server: `uv run uvicorn chapterverse.app:app --port 8765` with the env in R1.2, started by `eval/latency.py` as a subprocess so boot time can be measured (R3), and stopped at the end.
- SSE: parse `event:` and `data:` lines; reuse `chapterverse.contracts` types and `validate_order`. The receipt check: take the `fact` object bytes from the receipt data line exactly as `web/src` does (see `web/src/components/Receipt.tsx` or `web/src/api/`), sha256 them, compare with `answer_id`. If that is unclear, recompute `canonical_json` of the parsed fact and compare.
- Provider headers: the app does not expose Groq headers; read quota from the app's ledger log lines or call nothing extra. The simplest safe approach: count requests from the receipts' `calls` field and stop when the R1.3 caps are reached.
- Outputs: transcripts and per-question JSON under `SCRATCH/gates/` (create it).

## Checks before finishing

`uv run ruff check eval tests/test_eval_smoke.py`, `uv run ruff format --check eval tests/test_eval_smoke.py`, `uv run mypy eval`, `uv run pytest -q tests/test_eval_smoke.py`. All pass.

## Never

No git commands. No deploys, uploads, Modal or wrangler. Never print or write `GROQ_API_KEY`, `CLOUDFLARE_AI_WORKER_TOKEN` or `CLOUDFLARE_ACCOUNT_ID`. Never write inside `~/Code/legal-rag-router` or `~/Code/legal-rag-verifier`. Never read `~/Code/legal-rag-verifier/.router_key`.
