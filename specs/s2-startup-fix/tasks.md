# Startup fix: tasks

Files you own: `engine/chapterverse/app.py`, `engine/chapterverse/symspell_build.py`, `engine/chapterverse/store/reader.py` (only to add a public iteration method if needed), `tests/test_app.py`, `tests/test_slice2_integrate.py`, a new `tests/test_startup.py`. Edit nothing else. Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths), and `uv run python <script under $CV_SCRATCH/s2-startup-fix/>`. Nothing else: no cd, no pipes, no git.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `app.py` startup, `symspell_build.py`, `store/reader.py`.
- [x] 2. R1, R2.
- [x] 3. R3.
- [x] 4. R4; checks. Print the summary with the startup seconds and RSS.
