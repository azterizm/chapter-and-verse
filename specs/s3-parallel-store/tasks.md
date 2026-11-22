# S3.1c Parallel store decompression: tasks

Files you own: `engine/chapterverse/store/reader.py`, `tests/test_store.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python <script under $CV_SCRATCH/s3-parallel-store/>`, `uv run python -m cProfile ...`, `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `reader.py` (all of it) and `tests/test_store.py`.
- [x] 2. Measure the current `open_store` on the real store (seconds, peak RSS, buffer SHA-256) before changing anything.
- [x] 3. R1, R2.
- [x] 4. R3 tests.
- [x] 5. R4 measurements, then all steering checks. Print the summary with before and after numbers.
