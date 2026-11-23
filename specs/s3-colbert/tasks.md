# S3.3d ColBERT rerank: tasks

Files you own: new `engine/chapterverse/dense/colbert.py`, `engine/chapterverse/dense/__init__.py`, `engine/chapterverse/discovery.py`, `engine/chapterverse/pipeline.py` (only holding and passing the scorer), `engine/chapterverse/app.py` (only the ColBERT startup and its log line), `eval/discovery_gate.py`, new `tests/test_colbert.py`, `tests/test_dense_fusion.py`. Edit nothing else.

For real-data runs use: `STORE_DIR=artefacts/store`, `LEGAL_DATA_ROOT=~/Code/legal-rag-router/data`, dense index `artefacts/dense/index`, models `artefacts/models`. Env prefixes on the command line are not allowed, so pass them as gate arguments or read defaults in a scratch script.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.discovery_gate ...` (never `--split test`), `uv run python <script under $CV_SCRATCH/s3-colbert/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal, no pip or uv add.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, the two ColBERT config files, `engine/chapterverse/dense/*.py`, `discover_step`, `eval/discovery_gate.py`.
- [x] 2. R1 scorer and R2 windows, with tests.
- [x] 3. R3 rerank, R4 wiring.
- [x] 4. R5 gate tooling; run the dev gate with dense and ColBERT.
- [x] 5. R6 tests, steering checks. Print the summary: dev table RRF vs RRF + ColBERT, latency p50/p95, and whether ColBERT beats RRF on dev (MRR up and hit@10 not down).
