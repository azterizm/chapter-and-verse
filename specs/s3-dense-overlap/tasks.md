# S3.4a Dense load overlap and degrade: tasks

Files you own: `engine/chapterverse/app.py`, `tests/test_startup.py`, `tests/test_ship_dense.py`, `tests/test_app.py`. Edit nothing else (another worker edits `normalise.py` and its tests).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python <script under $CV_SCRATCH/s3-dense-overlap/>`, `grep`, `ls`, `cat`, `mkdir`, `cp`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Run every command in the foreground and wait for it.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `build_runtime` and the dense and ColBERT init functions in `app.py`, the startup tests.
- [x] 2. R1 overlap, R2 degrade.
- [x] 3. R3 tests.
- [x] 4. R4 measurement, then all steering checks. Print a short summary with the before and after seconds.
