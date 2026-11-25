# S5.3 Clarify ranking: tasks

Files you own: `engine/chapterverse/discovery.py`, `batteries/everyday/`, `eval/clarify_rank.py`, `eval/discovery_gate.py` (only the `--split` option), `tests/test_discovery.py`, new `tests/test_clarify_rank.py`, a new build script under `scripts/` and artefact under `artefacts/` if R3(a) needs them, and, only for R4 wiring, `engine/chapterverse/app.py` and `engine/chapterverse/pipeline.py` (smallest possible edit) and `docs/ARCHITECTURE.md`. Edit nothing else (other workers edit `audit/`, `eval/judge_gate.py`, `eval/heldout_modal.py`, `eval/legal_gate.py`, `eval/token_budget.py`, `batteries/legal/`, `web/` at the same time).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.clarify_rank ...`, `uv run python -m eval.discovery_gate ...` (dev split only; never the sealed test split), `uv run python <script under $CV_SCRATCH/s5-clarify-rank/ or scripts/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env. Run every command in the foreground and wait for it. Scratch: `$CV_SCRATCH/s5-clarify-rank/` (the path is in the prompt).

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, `everyday.md`, `engine/chapterverse/discovery.py`, `eval/discovery_gate.py`, how `pipeline.py` calls `discover_step`.
- [ ] 2. R1 battery.
- [ ] 3. R2 measurement, baseline numbers (dense on and off).
- [ ] 4. R3 diagnosis, then evaluate signals.
- [ ] 5. R4 wiring if a change qualifies; R5 tests.
- [ ] 6. R6 numbers, then all steering checks. Print the summary.
