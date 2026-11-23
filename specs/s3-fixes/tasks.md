# S3.2 Small fixes: tasks

Files you own: `engine/chapterverse/discovery.py`, `engine/chapterverse/templates.py`, `tests/test_discovery.py`, `tests/test_templates.py`, `tests/test_private_docs.py`. Edit nothing else (another worker is editing `app.py`, `pipeline.py`, `pii.py`, `symspell_build.py`, build and deploy files).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.probes_gate ...`, `uv run python -m eval.discovery_gate ...`, `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env. If an eval command needs network or keys, stop it and report instead.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `discovery.py`, `templates.py`, the probe gate `eval/probes_gate.py`.
- [x] 2. R1.
- [x] 3. R2, R3, R5.
- [x] 4. R4 tests.
- [x] 5. R6 gates, then all steering checks. Print the summary with the numbers.
- [x] 6. R7 (follow-up): narrow the named-person rule, tests, probes gate, checks. Print a short summary.
- [x] 7. R8: remove the possessive rule, tests, steering checks. Short summary.
