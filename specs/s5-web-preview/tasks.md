# S5.4 Web preview: tasks

Files you own: everything under `web/mock/`, `web/vite.config.ts`, `web/package.json` (scripts only, no dependency changes), `web/README.md`, `web/test/` (new tests and the mock test), `scripts/dev_engine.py`, `eval/web_smoke.py`, `tests/test_dev_engine.py`. Read anything; edit nothing else (other workers edit `eval/legal_gate.py`, `eval/token_budget.py`, `batteries/` at the same time). Do not edit `web/src/` (if a test needs a change there, report it).

Allowed shell commands, exactly: `npm run --prefix web test`, `npm run --prefix web typecheck`, `npm run --prefix web lint`, `npm run --prefix web build`, `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no npx, no npm install, never `mock:record`, `mock:record:engine` or `scripts/dev_engine.py`. Run every command in the foreground and wait for it. Scratch: `$CV_SCRATCH/s5-web-preview/` (the path is in the prompt).

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, `web/README.md`, `web/mock/record.py`, `web/mock/plugin.ts`, `web/vite.config.ts`, `engine/chapterverse/app.py` (startup, `/api/ask`, session token), `deploy/entrypoint.sh`.
- [ ] 2. R1 recorder (Python, with `tests/test_dev_engine.py` or a test for the recorder under `tests/` with a fake runtime).
- [ ] 3. R2 plugin and its tests.
- [ ] 4. R3 preview (vite proxy, `scripts/dev_engine.py`, README).
- [ ] 5. R4 smoke script.
- [ ] 6. R5 component tests, then the checks: web test, typecheck, lint, build, and the Python steering checks. Print a short summary, including whether the engine needs the session token and how the preview handles it.
