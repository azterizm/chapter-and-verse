# Probes resilience: tasks

Files you own: `eval/probes_gate.py`, `tests/test_eval_probes.py` (offline tests of the evaluators), `reports/probes-resilience.md`. Edit nothing else. Allowed shell commands, exactly: `uv run python -m eval.probes_gate --target local --out <dir>`, `uv run python -m eval.probes_gate --target live --base-url https://cv-origin.memonsystems.com --out <dir>`, `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q <your test file>`. Run the live target at most twice. Nothing else: no cd, no pipes, no git, no curl.

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, `eval/probes_gate.py`, and the probes repository files named above.
- [ ] 2. R1 to R3 and tests.
- [ ] 3. Run local; then live.
- [ ] 4. R4 report; checks. Print the summary with pass and fail counts per target and every failure.
