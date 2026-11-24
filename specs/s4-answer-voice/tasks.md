# S4.3 Answer voice: tasks

Files you own: `engine/chapterverse/audit/prompt.py`, `engine/chapterverse/audit/policy.py`, `engine/chapterverse/audit/continuation.py`, `engine/chapterverse/audit/modal_path.py`, `engine/chapterverse/audit/narration.py` (new), `engine/chapterverse/templates.py`, `engine/chapterverse/pipeline.py` (ONLY the `_cite` method and the `deliver` closure), and the tests for these modules (`tests/test_audit*.py`, `tests/test_continuation*.py`, `tests/test_modal_path*.py`, `tests/test_prompt*.py`, `tests/test_policy*.py`, `tests/test_templates.py`, new `tests/test_narration.py`, and any other test that pins `PROMPT_SHA256` or the refusal text). Edit nothing else (other workers write `eval/legal_gate.py`, `batteries/legal/`, `scripts/receipt.py`).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.probes_gate --target local`, `uv run python <script under $CV_SCRATCH/s4-answer-voice/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Never call a real provider. Run every command in the foreground and wait for it.

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, the files you own, `Policy` and `SentenceOut` in `contracts.py`, the existing tests of the generators.
- [ ] 2. R1, R2.
- [ ] 3. R3, R4 in both generators.
- [ ] 4. R5, R6.
- [ ] 5. R7 tests.
- [ ] 6. R8, then all steering checks. Print a summary: new PROMPT_SHA256, changed templates, changed test expectations.
