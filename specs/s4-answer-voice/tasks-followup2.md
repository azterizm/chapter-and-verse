# S4.3 follow-up 2: tasks

Files you own: `engine/chapterverse/audit/continuation.py`, `engine/chapterverse/audit/modal_path.py`, `tests/test_audit_generation.py`. Edit nothing else (another worker edits `eval/legal_gate.py` and its tests right now).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git. Never call a real provider. Run every command in the foreground and wait for it.

- [x] 1. Read the "Follow-up 2" section of `specs/s4-answer-voice/requirements.md` and both generators' abstention handling.
- [x] 2. R13.
- [x] 3. R14 tests, then all steering checks. Print a short summary.
