# S5.1a Token budget: tasks

Files you own: `eval/token_budget.py`, `tests/test_token_budget.py`. Edit nothing else (other workers edit `eval/legal_gate.py`, `batteries/`, `web/` and their tests at the same time). Read anything.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.token_budget ...`, `uv run python <script under $CV_SCRATCH/s5-token-budget/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env. Run every command in the foreground and wait for it. Scratch: `$CV_SCRATCH/s5-token-budget/` (the path is in the prompt).

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, `eval/legal_gate.py` (pipeline builder), `eval/run.py` (`NoModelRotation`), `engine/chapterverse/audit/verify.py`, `audit/prompt.py`, `providers/ledger.py`, `providers/rotation.py`, and the judge in the verifier package (find it with `uv run python -c "import legal_rag_verifier.judge as j; print(j.__file__)"`).
- [ ] 2. R1 to R5.
- [ ] 3. R6 tests.
- [ ] 4. R7 runs on real data, then all steering checks. Print the summary with both tables.
