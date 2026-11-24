# S4.6 Call budget: tasks

Files you own: `engine/chapterverse/providers/openai_compat.py`, `engine/chapterverse/providers/rotation.py`, `engine/chapterverse/audit/continuation.py`, `engine/chapterverse/audit/modal_path.py`, `engine/chapterverse/pipeline.py` (`_answer` only), and the tests of these modules. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.probes_gate --target local`, `uv run python <script under $CV_SCRATCH/s4-call-budget/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Never call a real provider. Run every command in the foreground and wait for it.

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, the adapters' timeouts, the rotation's failure and breaker handling, both generators' loops, `pipeline._answer`.
- [ ] 2. R1.
- [ ] 3. R2.
- [ ] 4. R3 measurement (scratch script, no provider calls).
- [ ] 5. R4 tests, probes gate, all steering checks. Print a short summary with the R3 numbers.

Note: another worker edits the ranked window selection in `pipeline.py` at the same time. Make small targeted edits to `_answer` only, never rewrite the file. If a test fails in code you do not own, report it and do not edit it.
