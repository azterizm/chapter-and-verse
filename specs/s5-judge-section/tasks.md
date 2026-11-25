# S5.1b2 Judge on the cited sections: tasks

Files you own: `engine/chapterverse/audit/verify.py`, `engine/chapterverse/audit/policy.py`, `engine/chapterverse/discovery.py` (only to move `_section_coordinate` out and import it back), the shared module you put the helper in, `eval/judge_gate.py`, `eval/heldout_modal.py`, `eval/token_budget.py`, `tests/test_judge_cited.py`, `tests/test_eval_judge_gate.py`, `tests/test_eval_heldout_modal.py`, `tests/test_token_budget.py`, new `tests/test_judge_section.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python -m eval.heldout_modal --dry ...`, `uv run python -m eval.judge_gate replay`, `uv run python -m eval.token_budget ...`, `uv run python -c "<one-liner>"`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no modal. Run every command in the foreground and wait for it. Never run `heldout_modal` without `--dry` or `judge_gate run`.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `specs/s5-judge-cited/requirements.md`, `reports/heldout-qwen-modal-cited.md`, `engine/chapterverse/audit/verify.py`, `audit/policy.py`, `eval/heldout_modal.py`, `eval/judge_gate.py`, `eval/token_budget.py`.
- [x] 2. R1 and R2.
- [x] 3. R3 and R4.
- [x] 4. R5 tests.
- [x] 5. R6 offline checks, then all steering checks. Print the summary with the dry-run numbers (calls, cost, characters, rows whose prompt differs from all scope) and the token budget medians for the section scope.
