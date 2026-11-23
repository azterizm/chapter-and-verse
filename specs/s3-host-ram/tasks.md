# S3.1e Keep the RAM copy across container restarts: tasks

Files you own: `deploy/oracle/setup.sh`, `deploy/compose.yml`, `deploy/entrypoint.sh`, `deploy/fetch_artefacts.py`, `deploy/README.md`, `engine/chapterverse/app.py` (only `_Maintenance.stop`), `tests/test_fetch_marker.py`, `tests/test_fetch_copy.py`, `tests/test_app.py` (only if a test asserts the join timeout). Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python <script under $CV_SCRATCH/s3-host-ram/>`, `uv run python deploy/fetch_artefacts.py ...` (only on directories under $CV_SCRATCH, never with HF_DATASET set), `sh -n deploy/entrypoint.sh`, `bash -n deploy/oracle/setup.sh`, `cp`, `mkdir`, `grep`, `ls`, `cat`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no docker, no sudo.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, and the files you own.
- [x] 2. R1 setup.sh and compose.yml.
- [x] 3. R2 entrypoint.
- [x] 4. R3 parallel verify, R4 shutdown join.
- [x] 5. R5 tests, R6 measurement, then all steering checks. Print the summary with the numbers and the three boot paths.
