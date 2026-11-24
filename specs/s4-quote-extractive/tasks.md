# S4.5 Quote extractive: tasks

Files you own: `engine/chapterverse/intent.py` (new), `engine/chapterverse/pipeline.py` (`_bounded` only), `engine/chapterverse/templates.py`, `web/src/api/events.ts`, `web/src/text.ts`, tests: new `tests/test_intent.py`, `tests/test_pipeline*.py`, `tests/test_templates.py`, web tests next to the two web files. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.legal_gate ...` (never `--split test`, never `--record`), `uv run python -m eval.run ...`, `uv run python -m eval.probes_gate --target local`, `npm run --prefix web test`, `npm run --prefix web build`, `npm run --prefix web lint`, `uv run python <script under $CV_SCRATCH/s4-quote-extractive/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env. Run every command in the foreground and wait for it.

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, `pipeline._bounded`, `extractive.py`, `templates.py`, the two web files and how the web renders an extractive event.
- [ ] 2. Run the R6 gates BEFORE; save outputs in scratch.
- [ ] 3. R1, R2, R3.
- [ ] 4. R4.
- [ ] 5. R5 tests.
- [ ] 6. R6 gates AFTER, then all steering checks and the web test, lint and build (only those whose npm scripts exist). Print a short summary.
