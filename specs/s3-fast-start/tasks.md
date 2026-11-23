# S3.1b Restart under 20 s: tasks

Files you own: `engine/chapterverse/app.py`, `engine/chapterverse/normalise.py` (only the dictionary loader), `engine/chapterverse/symspell_build.py`, `deploy/entrypoint.sh`, `deploy/fetch_artefacts.py`, `deploy/README.md`, `tests/test_app.py`, `tests/test_startup.py`, `tests/test_build.py`, `tests/test_symspell_artefact.py`, `tests/test_normalise.py`, new `tests/test_fetch_marker.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python <script under $CV_SCRATCH/s3-fast-start/>`, `uv run python -m cProfile ...`, `cp`, `sh -n deploy/entrypoint.sh`, `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, and the files you own.
- [x] 2. R1 pin check and purge off the readiness path, concurrent checks.
- [x] 3. R2 hash once per boot.
- [x] 4. R3 profile and speed up or overlap the dictionary load; identity test.
- [x] 5. R4 measure, then all steering checks. Print the summary with the numbers and the initial provider state.
