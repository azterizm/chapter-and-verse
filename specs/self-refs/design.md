# Self references: design

| File | Change |
|---|---|
| `engine/chapterverse/audit/refs.py` | `check_refs(..., cited_texts=None, cited_coordinates: Mapping[str, str] \| None = None)`; the grounded set adds the canonical forms of each cited coordinate's tail and its ancestors' tails, in the verifier's canonical citation form (see `legal_rag_verifier.citations`: canonical values look like `s124/1ZA/a`), and resolves relative references against the cited passage's section |
| `engine/chapterverse/audit/verify.py` | Only the `check_refs` call: also pass passage id to coordinate (`Passage.coordinate` from `passages_by_id(premise)`). Keep the classification check exactly as it is |
| `tests/test_audit_refs.py` | R2.1 |

Read `audit/refs.py` (current grounding code using `find_citations` and `extract_claims`) and `legal_rag_verifier/citations.py` before changing anything. Do not edit `audit/prompt.py`.
