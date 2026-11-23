# S3.3a Dense bake-off: tasks

Files you own: new `engine/chapterverse/dense/__init__.py`, `engine/chapterverse/dense/doctext.py`, `engine/chapterverse/dense/embed.py`, new `eval/dense_bakeoff.py`, new `tests/test_dense_doctext.py`, `tests/test_dense_embed.py`. Edit nothing else.

Environment for real-data runs (pass on the command line as `KEY=value` prefixes is NOT allowed; instead the script reads defaults): `STORE_DIR` default `artefacts/store`, `LEGAL_DATA_ROOT` default `~/Code/legal-rag-router/data`, models dir default `artefacts/models`. Make the bake-off script take `--store`, `--data-root`, `--models` arguments with those defaults so no env is needed.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.dense_bakeoff ...`, `uv run python <script under $CV_SCRATCH/s3-dense-bakeoff/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no pip or uv add.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `engine/chapterverse/store/reader.py` public methods, `eval/discovery_gate.py`, the router's `concepts.py` and `discovery.py` (read only, in `~/Code/legal-rag-router/src/legal_rag_router/`).
- [x] 2. R1 document text and tests.
- [x] 3. R2 embedder and tests.
- [ ] 4. R3 bake-off script; run it on the real data (dev only). This takes a while: run it in the foreground and wait.
- [ ] 5. Steering checks. Print the summary: the table of hit@1/5/10 and MRR for BM25F, each model dense-only and RRF; throughput; the fields used for the prefix; your recommended model by dev RRF MRR (ties by hit@10, then throughput).
