# S4.3 follow-up: tasks

Files you own: `engine/chapterverse/audit/continuation.py`, `engine/chapterverse/audit/modal_path.py`, `engine/chapterverse/audit/prompt.py`, `engine/chapterverse/audit/policy.py`, `engine/chapterverse/audit/narration.py`, `tests/test_audit*.py`, `tests/test_narration.py`, `tests/test_modal_path*.py`. Edit nothing else (another worker is editing `pipeline.py`, `app.py`, `dense/store.py` and their tests right now).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env. Never call a real provider. Run every command in the foreground and wait for it. If a full-suite failure is in a file you do not own, report it and do not edit it.

- [ ] 1. Read requirements.md section "Follow-up" (R9 to R12) and the generators' loops.
- [ ] 2. R11.
- [ ] 3. R9, R10 in both generators.
- [ ] 4. R12 tests, then all steering checks. Print a short summary.
