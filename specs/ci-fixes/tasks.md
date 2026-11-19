# CI fixes: tasks

Files you own: `tests/test_audit_classify.py`, `tests/test_app.py`. Edit nothing else.

- [x] 1. Read `specs/STEERING.md` and `requirements.md`.
- [x] 2. R1.
- [x] 3. R2; loop the disconnect test 50 times and record the pass count.
- [x] 4. Run the full test suite with the real store hidden: `STORE_DIR=/nonexistent LEGAL_DATA_ROOT= uv run pytest -q` must pass (data tests skip). Then the steering checks. Print the summary.
