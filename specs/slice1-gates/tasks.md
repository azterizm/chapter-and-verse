# Slice 1 gates: tasks

Mark each task `[x]` in this file when done. Work in order.

- [x] 1. Read `requirements.md`, `design.md`, `docs/ARCHITECTURE.md` §5, §10, §11, `engine/chapterverse/app.py`, `engine/chapterverse/contracts.py` (`validate_order`, `encode_sse`, `AnswerFactObject`). (R1, R2)
- [x] 2. Run the checks on `eval/run.py`; fix only lint, format or type errors. (design)
- [x] 3. Write `batteries/slice1_questions.jsonl` (20 rows). Use the real data to pick provisions that exist: confirm each citation with `uv run python -c "from legal_rag_router import Router; r=Router.from_path('$HOME/Code/legal-rag-router/data/index'); print(r.route('<query>').status)"`. (R1.1)
- [x] 4. Write `eval/smoke.py` and `tests/test_eval_smoke.py`; tests pass offline. (R2)
- [x] 5. Write `eval/latency.py`: start uvicorn, time `/api/health`, measure RSS, send the 20 questions one at a time with 10 s between them, save transcripts, enforce the R1.3 caps, stop the server. (R1.2 to R1.5, R3)
- [x] 6. Run `eval/latency.py` once. Then run `eval/smoke.py` against a fresh local server. (R1, R2)
- [x] 7. Read every generated answer against its sources; list wrong and vague sentences with quotes. (R1.6)
- [x] 8. Write `reports/slice1-gates.md`. (R4)
- [x] 9. Run the design's checks; all pass. Print a final summary of under 250 words: per gate numbers and pass or fail, requests used per provider, findings.
