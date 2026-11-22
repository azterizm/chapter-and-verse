# S3.1d Copy and verify in one pass: tasks

Files you own: `deploy/fetch_artefacts.py`, `deploy/entrypoint.sh`, `deploy/README.md`, `tests/test_fetch_marker.py`, new `tests/test_fetch_copy.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python <script under $CV_SCRATCH/s3-copy-verify/>`, `uv run python deploy/fetch_artefacts.py ...` (only on directories under $CV_SCRATCH, never with HF_DATASET set), `sh -n deploy/entrypoint.sh`, `cp`, `mkdir`, `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `deploy/fetch_artefacts.py`, `deploy/entrypoint.sh`, `tests/test_fetch_marker.py`.
- [x] 2. R1.
- [x] 3. R2.
- [x] 4. R3 tests.
- [x] 5. R4 measurement, then all steering checks. Print the summary with the numbers.
