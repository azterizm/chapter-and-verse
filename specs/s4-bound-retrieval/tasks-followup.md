# S4.4 follow-up: tasks

Files you own: `engine/chapterverse/bound_rank.py`, `engine/chapterverse/pipeline.py` (ONLY the ranked window selection code you added in 851aeb1; another worker edits `_answer` in the same file right now: make small targeted edits, never rewrite the file), `tests/test_bound_rank.py`. Edit nothing else.

Allowed shell commands: exactly those in `tasks.md` of this spec. Scratch: `$CV_SCRATCH/s4-bound-retrieval/followup/`. If a test fails in a file you do not own, or in `_answer`, report it and do not edit it.

- [x] 1. Read the "Follow-up" section of requirements.md and the router's `_candidates` and `_prior`.
- [x] 2. R7.
- [x] 3. R8.
- [x] 4. Tests for R7 (a Scotland-flagged section is demoted, and not demoted when the query names Scotland) and R8 (allocation rounds on a fixture).
- [x] 5. R9 gates and all steering checks. Print a summary table: mode, rows passed, must_reach recall, should_reach recall, before and after.
