# S3.3b Embed on Modal: tasks

Files you own: `engine/chapterverse/dense/embed.py`, `engine/chapterverse/dense/doctext.py` (only if needed by R2), new `deploy/modal/embed.py`, `eval/dense_bakeoff.py`, `tests/test_dense_embed.py`, new `tests/test_modal_embed.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python <script under $CV_SCRATCH/s3-modal-embed/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: never run `modal`, no cd, no pipes, no redirection, no git, no curl, no env, no pip or uv add. `modal` may not be importable in the test environment: guard the import in tests (skip what needs it) and keep the order logic in a function that does not import `modal`.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `specs/s3-dense-bakeoff/requirements.md`, `engine/chapterverse/dense/*.py`, `eval/dense_bakeoff.py`, `deploy/modal/ledger.py`, `deploy/modal/serve.py` (for the Modal style used here).
- [x] 2. R1.
- [x] 3. R2.
- [x] 4. R3.
- [x] 5. R4 tests, then all steering checks. Print the summary with the exact `modal run` command lines the orchestrator should use for (a) the gte-small pool and (b) the full corpus with a chosen model.
