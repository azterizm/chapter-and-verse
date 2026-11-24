# S4.7 Record local: tasks

Files you own: `eval/legal_gate.py`, `tests/test_legal_gate.py`. Edit nothing else (other workers edit `pipeline.py`, `continuation.py`, `templates.py`, `intent.py`, web files and their tests right now).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git, never `--record`. Run every command in the foreground and wait for it. If a full-suite failure is in a file you do not own, report it and do not edit it.

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, `eval/legal_gate.py`, `app.build_runtime` (read only).
- [ ] 2. R1, R2, R3.
- [ ] 3. R4 tests, then the steering checks. Print a short summary.
