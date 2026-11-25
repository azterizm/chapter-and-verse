# S5.3b Ship the clarify prior: tasks

Files you own: `scripts/build_clarify_prior.py`, `build/publish_dataset.py`, `build/README.md`, `deploy/fetch_artefacts.py`, `deploy/entrypoint.sh`, `deploy/README.md`, `deploy/engine.env.example`, `eval/discovery_gate.py` and `eval/clarify_rank.py` (only the `--prior` default), their tests under `tests/`, and the new files under `artefacts/clarify/`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python <script under $CV_SCRATCH/s5-ship-prior/>`, `uv run python -m build.publish_dataset --help`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no hf, no upload (never `publish_dataset` without `--help`). Run every command in the foreground and wait for it.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `build/publish_dataset.py`, `deploy/fetch_artefacts.py`, `deploy/entrypoint.sh`, `scripts/build_clarify_prior.py`.
- [x] 2. R1 (print the sha256 of the copied file; it must equal the sealed one).
- [x] 3. R2, R3, R4.
- [x] 4. R5 tests, then all steering checks. Print a short summary.
