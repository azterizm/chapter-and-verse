# S3.3c Dense index, retriever and fusion: tasks

Files you own: new `build/dense_index.py`, new `engine/chapterverse/dense/store.py`, new `engine/chapterverse/dense/retriever.py`, `engine/chapterverse/dense/__init__.py`, `engine/chapterverse/discovery.py`, `engine/chapterverse/pipeline.py` (only passing the retriever to `discover_step` and holding it), `engine/chapterverse/app.py` (only the dense startup in `build_runtime` and its log line), `eval/discovery_gate.py`, new `tests/test_dense_store.py`, `tests/test_dense_fusion.py`, `tests/test_discovery.py`, `tests/test_startup.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python build/dense_index.py ...`, `uv run python -m eval.discovery_gate ...` (never `--split test`), `uv run python <script under $CV_SCRATCH/s3-vector-store/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal, no pip or uv add.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `engine/chapterverse/dense/*.py`, `discovery.py`, the `discover_step` call sites in `pipeline.py`, `build_runtime` in `app.py`, `eval/discovery_gate.py`, the router's `discovery.py` (read only).
- [x] 2. R1 index build; build an index from the 65,536-row file under `$CV_SCRATCH/s3-vector-store/idx65k`.
- [x] 3. R2 store, R3 retriever.
- [x] 4. R4 fusion, R5 wiring.
- [x] 5. R6 gate tooling, R7 tests.
- [x] 6. R8 measurements, then all steering checks. Print the summary with the numbers.
