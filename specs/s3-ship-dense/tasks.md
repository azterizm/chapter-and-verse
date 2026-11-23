# S3.3e Ship the dense index and model: tasks

Files you own: `build/publish_dataset.py`, `build/dense_index.py` (only if R4 needs it), `engine/chapterverse/dense/store.py` (only if R4 needs it), `deploy/fetch_artefacts.py`, `deploy/entrypoint.sh`, `deploy/oracle/setup.sh`, `deploy/README.md`, `build/README.md`, `tests/test_build.py`, `tests/test_fetch_marker.py`, `tests/test_fetch_copy.py`, `tests/test_dense_store.py`, new `tests/test_ship_dense.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python build/publish_dataset.py ...` (dry run only, never `--yes`), `uv run python build/dense_index.py ...` (only with `--out` under `$CV_SCRATCH`), `uv run python deploy/fetch_artefacts.py ...` (only on directories under `$CV_SCRATCH`, never with HF_DATASET set), `uv run python <script under $CV_SCRATCH/s3-ship-dense/>`, `sh -n deploy/entrypoint.sh`, `bash -n deploy/oracle/setup.sh`, `grep`, `ls`, `cat`, `mkdir`, `cp`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal, no hf, no pip or uv add. Run long commands in the foreground and wait for them.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, and the files you own; `app.py` dense startup; `chapterverse/dense/embed.py` `load_model`.
- [x] 2. R4 manifest format, R1 publish.
- [x] 3. R2 fetch, R3 entrypoint, R5 host RAM, README.
- [x] 4. R6 tests.
- [x] 5. R7 dry run, then all steering checks. Print the summary with the dry-run file list (paths and sizes) and total.
