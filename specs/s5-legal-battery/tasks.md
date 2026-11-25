# S5.2 Legal battery: tasks

Files you own: `batteries/legal/legal.jsonl`, `batteries/legal/SEAL.json`, `eval/legal_gate.py`, `eval/legal_answers.py`, `tests/test_legal_gate.py`, `tests/test_legal_answers.py`. Edit nothing else (other workers edit `eval/token_budget.py`, `web/` and their tests at the same time).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.legal_gate --split dev ...` (never `--split test`, never `--record`), `uv run python <script under $CV_SCRATCH/s5-legal-battery/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, never `eval.legal_answers`. Run every command in the foreground and wait for it. Scratch: `$CV_SCRATCH/s5-legal-battery/` (the path is in the prompt).

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `rows.md`, `specs/s4-legal-battery/requirements.md`, `eval/legal_gate.py`, `chapterverse/audit/policy.py`.
- [x] 2. R1: resolve and verify every new row with a scratch script; write the rows.
- [x] 3. R2 schema, R3 split and seal.
- [x] 4. R4 answer-level run module.
- [x] 5. R5 tests.
- [x] 6. R6 dev gate on real data, then all steering checks. Print a summary: old and new dev numbers, failing rows with missing items, every CHECK note, any L01 to L40 provision not in force.
