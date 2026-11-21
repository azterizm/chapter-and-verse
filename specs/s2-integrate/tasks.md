# Slice 2 integration: tasks

Files you own: `engine/chapterverse/pipeline.py`, `engine/chapterverse/app.py`, `engine/chapterverse/normalise.py`, `engine/chapterverse/audit/prompt.py`, a new `engine/chapterverse/symspell_build.py` if needed, `build/symspell_dict.py`, and tests under `tests/`. Edit `web/` only if R4 shows a gap. The only shell commands you may run: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with a test path), and `npm run test` in web. Never run git or anything else.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, the four component modules and `pipeline.py`, `app.py`.
- [x] 2. R1 steps 1 to 5 and R2.
- [x] 3. R1 steps 6 to 8.
- [x] 4. R3 tests; R4 checks. Print the summary with the old and new PROMPT_SHA256 and the startup time added.
