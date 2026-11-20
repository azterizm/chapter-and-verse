# Classification check: design

## Files you own

| File | Change |
|---|---|
| `engine/chapterverse/audit/classify.py` | New. `CLASSIFICATION_SHIFT: Final = "CLASSIFICATION_SHIFT"`; `classification_failure(sentence: str, cited_texts: Sequence[str]) -> str \| None` returning a detail string or None; the term classes as a frozen mapping |
| `engine/chapterverse/audit/verify.py` | In `AuditVerifier.check_sentence`, after the reference check passes and before the judge runs: collect the texts of the cited passages (passage ids map to premise passages in premise order, the same numbering as `chapterverse.windows.passages_by_id`), call `classification_failure`, and on failure return a ROLLBACK verdict built the same way the reference failures are (`Reason.UNGROUNDED_CITATION` as the enum, `"CLASSIFICATION_SHIFT: <detail>"` in details). Add the code to whatever set `reason_codes` uses |
| `tests/test_audit_classify.py` | New. R2.1 to R2.4 |

Read first: `audit/verify.py`, `audit/refs.py` (how `REF_CODES` and failure verdicts are built), `audit/continuation.py` (how a rollback flows), `tests/test_audit_generation.py` (fakes to reuse), `engine/chapterverse/windows.py` (`passages_by_id`).

Do not edit `audit/refs.py`: another worker will change it after you.

## Regression script

`SCRATCH/classification-check/regress.py`, run with `uv run python`. It reads the gate transcripts, rebuilds each answer's passage texts from its `sources` event, maps each sentence's `passage_ids` to texts, and calls `classification_failure`.
