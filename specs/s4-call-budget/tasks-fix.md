# S4.6 fix: tasks

Files you own: `engine/chapterverse/audit/continuation.py`, `tests/test_audit_generation.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git. Run every command in the foreground and wait for it.

- [x] 1. Read the "Fix" section of `specs/s4-call-budget/requirements.md` and `_audit_sentences`.
- [x] 2. R5.
- [x] 3. R6 tests, then all steering checks. Print a short summary.
