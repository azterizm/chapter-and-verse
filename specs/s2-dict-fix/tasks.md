# Dictionary fix: tasks

Files you own: `build/symspell_dict.py`, `engine/chapterverse/symspell_build.py` (if it exists), `engine/chapterverse/normalise.py`, `tests/test_normalise.py`. Edit nothing else. Allowed shell commands, exactly: `uv run python build/symspell_dict.py` (with its flags), `uv run python -m eval.discovery_gate --out <dir>`, `uv run python <a script you write under $CV_SCRATCH/s2-dict-fix/>`, `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q tests/test_normalise.py`, `uv run pytest -q`. Nothing else: no cd, no pipes, no git.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `build/symspell_dict.py`, `engine/chapterverse/normalise.py`.
- [x] 2. R1: rebuild the dictionary into artefacts/symspell/.
- [x] 3. R2 and tests.
- [x] 4. R3 regression.
- [x] 5. R4 gate rerun; checks. Print the summary with the R3 change list and the R4 numbers.
