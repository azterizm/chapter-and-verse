# Classification check: tasks

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `design.md` and the files design.md lists.
- [x] 2. Write `audit/classify.py`. (R1.1 to R1.5)
- [x] 3. Wire it into `AuditVerifier.check_sentence` in `audit/verify.py`. (R1.6)
- [x] 4. Write `tests/test_audit_classify.py`; find the real offence provision with the store (`uv run python -c "from chapterverse.store import open_store; s=open_store('artefacts/store'); ..."`). (R2)
- [x] 5. Measure p99 latency of `classification_failure` over 1,000 runs. (R1.5)
- [x] 6. Run the regression over the gate transcripts. (R3)
- [x] 7. Run the steering checks; print the summary, including the p99 and the regression counts.
