# S3.1 Ship the spelling dictionary: tasks

Files you own: `build/symspell_dict.py`, `build/publish_dataset.py`, `engine/chapterverse/symspell_build.py`, `engine/chapterverse/app.py` (only `_load_slice2_dictionary` and what calls it), `engine/chapterverse/pipeline.py` (only `_get_dictionary`, the vocabulary, and the two `mask_pii` calls), `engine/chapterverse/pii.py`, `deploy/fetch_artefacts.py`, `deploy/entrypoint.sh`, `deploy/README.md`, `build/README.md`, `tests/test_startup.py`, `tests/test_pii.py`, `tests/test_build.py`, `tests/test_slice2_integrate.py`, `tests/test_normalise.py`, a new `tests/test_symspell_artefact.py`, and `artefacts/symspell/`. Edit nothing else (another worker is editing `discovery.py`, `templates.py`, `tests/test_discovery.py`, `tests/test_templates.py`).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python build/symspell_dict.py ...`, `uv run python build/publish_dataset.py ...` (dry run only, never `--yes`), `uv run python <script under $CV_SCRATCH/s3-dict-artefact/>`, `uv run python -m cProfile ...`, `sh -n deploy/entrypoint.sh`, `shellcheck deploy/entrypoint.sh`, `grep`, `ls`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, and the files you own.
- [x] 2. R1 build and manifest.
- [x] 3. R2 publish (dry run only).
- [x] 4. R3 fetch, R4 entrypoint, README.
- [x] 5. R5 runtime load with verification and fallback.
- [x] 6. R6 one vocabulary for PII.
- [x] 7. R7 tests.
- [x] 8. R8 measurements, then all steering checks. Print the summary with the numbers.
