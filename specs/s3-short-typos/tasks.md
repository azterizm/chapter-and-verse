# S3.4b Short words and gibberish: tasks

Files you own: `engine/chapterverse/normalise.py`, `tests/test_normalise.py`, `tests/test_slice2_integrate.py`. Edit nothing else (another worker edits `app.py` and startup tests).

Real-data paths: store `artefacts/store`, data root `~/Code/legal-rag-router/data`, dictionary `artefacts/symspell/dictionary.txt`, dense index `artefacts/dense/index`, models `artefacts/models`. Env prefixes on the command line are not allowed: read these as defaults inside your scratch scripts, and pass the gate's own flags.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.discovery_gate ...` (never `--split test`), `uv run python <script under $CV_SCRATCH/s3-short-typos/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Run every command in the foreground and wait for it.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `normalise.py`, `tests/test_normalise.py`, the pipeline's normalise call in `pipeline.py` (read only).
- [x] 2. Run the R4 script and the R5 dev gate BEFORE changing anything; save outputs under your scratch dir.
- [x] 3. R1, R2, R3 tests, R6 test.
- [x] 4. R4 and R5 AFTER; compare.
- [x] 5. All steering checks. Print a short summary: the changed queries list, dev hit@10 and MRR before and after.
