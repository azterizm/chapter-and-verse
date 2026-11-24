# S4.1 Legal battery: tasks

Files you own: `batteries/legal/legal.jsonl`, `batteries/legal/SEAL.json`, `eval/legal_gate.py`, `tests/test_legal_gate.py`. Edit nothing else (other workers edit `engine/`, `scripts/` and other tests at the same time).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.legal_gate ...` (never `--split test`, never `--record`), `uv run python <script under $CV_SCRATCH/s4-legal-battery/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Run every command in the foreground and wait for it. Scratch: `$CV_SCRATCH/s4-legal-battery/` (the path is in the prompt).

- [ ] 1. Read `specs/STEERING.md`, `requirements.md`, `rows.md`, `eval/run.py` (pipeline setup, `NoModelRotation`), `eval/probes_gate.py` (client, pacing), `eval/discovery_gate.py` (dense options), `chapterverse/audit/policy.py`.
- [ ] 2. R1: resolve and verify every row with a scratch script; write the battery.
- [ ] 3. R2 seal, R3 retrieval gate.
- [ ] 4. R4 rubric and recorder.
- [ ] 5. R5 tests.
- [ ] 6. R6 dev gate on real data, then all steering checks. Print a summary: rows passed on dev, must_reach recall, each failing row with its missing items, every CHECK note.
