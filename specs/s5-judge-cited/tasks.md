# S5.1b Judge on cited passages: tasks

Files you own: `engine/chapterverse/audit/verify.py`, `engine/chapterverse/audit/policy.py`, `eval/judge_gate.py`, `eval/heldout_modal.py`, `tests/test_eval_judge_gate.py`, `tests/test_eval_heldout_modal.py`, new `tests/test_judge_cited.py`, and any existing test that asserts the policy hash or verifier config name (update the expected value only, and list each in your summary). Edit nothing else (other workers edit `eval/legal_gate.py`, `eval/token_budget.py`, `batteries/`, `web/`, `scripts/` at the same time).

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.heldout_modal --dry ...`, `uv run python -m eval.judge_gate replay ...`, `uv run python -c "<one-liner>"`, `uv run python <script under $CV_SCRATCH/s5-judge-cited/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Run every command in the foreground and wait for it. Scratch: `$CV_SCRATCH/s5-judge-cited/` (the path is in the prompt).

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `engine/chapterverse/audit/verify.py`, `audit/policy.py`, the verifier's `verifier.py` (`check_sentence`, `_judge`, `_run_nli`, `_nli_candidates`) and `judge.py` (`Judge.score`, `one_hot`, `user_prompt`), `eval/judge_gate.py`, `eval/heldout_modal.py`.
- [x] 2. Before writing the gate rule, record in `$CV_SCRATCH/s5-judge-cited/R3-fields.md` which coordinate fields held-out and fresh2 units carry (with one example each).
- [x] 3. R1 and R2 (engine).
- [x] 4. R3 (gates).
- [x] 5. R4 tests.
- [x] 6. R5 offline checks, then all steering checks. Print the summary with both dry-run cost estimates and character totals, and the replay result.
