# Route raw first: tasks

Files you own: `engine/chapterverse/nlp.py`, `engine/chapterverse/pipeline.py`, `tests/test_nlp.py`, `tests/test_pipeline.py`, `tests/test_slice2_integrate.py`. Edit nothing else. Allowed shell commands, exactly: `uv run python -m eval.run --out <dir>`, `uv run python <a script you write under $CV_SCRATCH/s2-route-first/>`, `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths). Nothing else: no cd, no pipes, no git.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `nlp.py`, the routing part of `pipeline.py`.
- [ ] 2. R1.
- [ ] 3. R2, R3.
- [ ] 4. R4; checks. Print the summary with the classified differences.
