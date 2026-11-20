# Grounded references: design

## Files you own

| File | Change |
|---|---|
| `engine/chapterverse/audit/refs.py` | `check_refs(sentence, known_ids, cited_texts: Mapping[str, str] \| None = None)`: when `cited_texts` (passage id to text) is given, a model-written provision reference grounded in the text of a passage the sentence cites passes (R1). With `cited_texts=None` behaviour is unchanged |
| `engine/chapterverse/audit/verify.py` | Only the call to `check_refs` in `AuditVerifier.check_sentence`: pass the cited passages' texts. Another worker added a classification check in this method; keep it exactly as it is |
| `engine/chapterverse/audit/prompt.py` | R2 |
| `tests/test_audit_refs.py`, `tests/test_audit_prompt.py` | R3.1, R3.2 |

Normalise references with the verifier's own citation parsing where possible (`legal_rag_verifier.citations`, `legal_rag_verifier.claims.extract_claims` CITATION kind), so that sentence and passage are compared on the same canonical form (for example `s124/1ZA/a`). Read `audit/refs.py` first to see how references are found today.
