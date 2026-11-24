# S4.4 Bound retrieval: tasks

Files you own: `engine/chapterverse/bound_rank.py` (new), `engine/chapterverse/pipeline.py` (`select_windows` area, `_read`, `_bounded`, `Selection`, `Pipeline.__init__` for the new dependency; do not touch `_cite`, `deliver` or the generators), `engine/chapterverse/app.py` (`build_runtime` wiring only), `engine/chapterverse/dense/store.py` (`score_rows` only), tests: new `tests/test_bound_rank.py`, and `tests/test_pipeline*.py` / `tests/test_dense*.py` where needed. Edit nothing else.

Data defaults (read inside your scratch scripts and the gates' own defaults; env prefixes on the command line are not allowed): store `artefacts/store`, data root `~/Code/legal-rag-router/data`, dense index `artefacts/dense/index`, models `artefacts/models`, dictionary `artefacts/symspell/dictionary.txt`.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.legal_gate ...` (never `--split test`, never `--record`), `uv run python -m eval.discovery_gate ...` (never `--split test`), `uv run python -m eval.run ...`, `uv run python -m eval.probes_gate --target local`, `uv run python <script under $CV_SCRATCH/s4-bound-retrieval/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Run every command in the foreground and wait for it.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `pipeline.py` (`select_windows`, `_read`, `_bounded`), `discovery.py` (`_rrf_fuse_candidates`, dense use), `dense/store.py`, `dense/retriever.py`, and the router's `concepts.py` and `discovery.py` (`_score`, `DEFAULT_DISCOVERY`) in the installed package (read only).
- [x] 2. Run gates R6 a to d BEFORE any change; save outputs in scratch.
- [x] 3. R1, R2 (with `score_rows`).
- [x] 4. R3, R4.
- [x] 5. R5 tests.
- [x] 6. R6 gates AFTER, then all steering checks. Print a summary with every before and after number, the rows that changed, and the latency.
