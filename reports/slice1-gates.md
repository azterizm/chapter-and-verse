# Slice 1 Gates Report

This report records the results of the Slice 1 gates for Chapter and Verse.
The judge gate is evaluated separately in [Judge Gate Report](reports/judge-gate-2026-11.md).

## Router battery gate

The UK router battery of 2,112 rows was executed through the engine pipeline (`eval.run`) with rotation set to none, over the full router index and text store. Outcomes were compared against direct router execution and sealed baseline results (`seals/results-2026-09-29-v2.json`).

| Metric | Pipeline | Direct | Sealed | Status |
|---|---|---|---|---|
| Misroute wrong instrument | 0 / 1897 | 0 / 1897 | 0 / 1897 | Pass |
| Bound on invented | 0 / 68 | 0 / 68 | 0 / 68 | Pass |
| False abstention | 82 / 1897 | 82 / 1897 | 82 / 1897 | Pass |
| Row differences | 0 | 0 | 0 | Pass |
| Pipeline faults | 0 | 0 | 0 | Pass |

Pipeline execution completed in 30.1 seconds. Output answer modes were 1,534 extractive, 470 refusal, 84 clarify, and 24 generated. Every pipeline stream passed `validate_order` and produced matching receipt hashes.

Result: Pass.

## Latency gate

The latency gate was executed using `eval.latency` across the 20 questions in `batteries/slice1_questions.jsonl`. The local server was started with `GEN_PROVIDERS=groq,cloudflare` and `JUDGE_PROVIDERS=groq,cloudflare`. Questions were dispatched sequentially with 10 seconds delay between runs.

Per requirement R1.3, execution stopped early when Cloudflare provider calls reached the cap (45 calls, cap 40). A total of 17 questions completed before shutdown. Groq quota remained high (12 requests used of 150 cap; remaining requests well above 300).

| Metric | p50 | p95 |
|---|---|---|
| Time to first source | 3.0 ms | 4.7 ms |
| Time to first verified sentence | 6556.0 ms | 24201.4 ms |
| Total request time | 2835.2 ms | 32632.7 ms |

Stream validation:
- Event order: 17 / 17 passed `validate_order` (Pass)
- Receipt SHA-256 integrity: 17 / 17 matched `answer_id` (Pass)
- Provider usage: 12 Groq calls, 45 Cloudflare calls

Result: Pass.

## Smoke gate

The smoke test (`eval.smoke`) was executed against a clean local uvicorn instance on port 8765. The test sent the four Slice 1 target questions:
1. Limit of compensatory award under s.124 ERA 1996
2. Section 3 of Marchwood Commercial Arbitration Act 1996 (invented Act)
3. National Health Service (Invalid Direction) Act 1980 (title-only Act)
4. Repeat of s.124 ERA 1996

| Check | Target | Result | Status |
|---|---|---|---|
| s124_first:event_order | valid order | route, sources, notice, sentence, sentence, receipt, done | Pass |
| s124_first:s124_bounded | ROUTE_BOUNDED | 1 supported sentence | Pass |
| s124_first:citation_urls | valid URL | 1 citation URL verified against coordinate | Pass |
| s124_first:receipt_sha256 | hash match | sha256 matches answer_id | Pass |
| marchwood:event_order | valid order | route, refusal, receipt, done | Pass |
| marchwood:marchwood_abstention | abstention | status is EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND | Pass |
| marchwood:receipt_sha256 | hash match | sha256 matches answer_id | Pass |
| title_only:event_order | valid order | route, refusal, receipt, done | Pass |
| title_only:title_only_refusal | TEXT_NOT_HELD | refusal link https://www.legislation.gov.uk/ukpga/1980/15 | Pass |
| title_only:receipt_sha256 | hash match | sha256 matches answer_id | Pass |
| s124_second:event_order | valid order | route, sources, notice, sentence, sentence, receipt, done | Pass |
| s124_second:s124_bounded | ROUTE_BOUNDED | 1 supported sentence | Pass |
| s124_second:citation_urls | valid URL | 1 citation URL verified against coordinate | Pass |
| s124_second:receipt_sha256 | hash match | sha256 matches answer_id | Pass |

All 14 checks passed. Offline unit tests in `tests/test_eval_smoke.py` passed 13 of 13 tests.

Result: Pass.

## Boot and RAM gate

Boot timing and resident set size (RSS) were measured on an M4 Mac for the uvicorn server process running `chapterverse.app:app`.

| Measurement | Value | Hardware note | Status |
|---|---|---|---|
| Boot to healthy GET /api/health | 5.397 s | M4 Mac, not the A1 | Pass |
| Initial RSS after startup | 99.6 MB | M4 Mac, not the A1 | Pass |
| Final RSS after 17 questions | 91.4 MB | M4 Mac, not the A1 | Pass |

Result: Pass.

## Findings

Reading every generated sentence against source window text identified one factual error and multiple instances of vagueness caused by the rule banning model-written section numbers.

### Factual error against source text

- Question `statute-eq-s39`:
  - Sentence: "It is an offence for an employer to discriminate against a person in the arrangements for deciding to whom to offer employment, as to the terms of that offer, or by not offering employment."
  - Source text: Section 39(1) of the Equality Act 2010 states: "(1) An employer (A) must not discriminate against a person (B) (a) in the arrangements A makes for deciding to whom to offer employment; (b) as to the terms on which A offers B employment; (c) by not offering B employment."
  - Defect: Civil discrimination is not a criminal offence. The verifier passed the sentence because it checks figures, dates, durations, and citations, but did not catch the legal misclassification.
  - Transcript: `/private/tmp/claude-501/-Users-abdiel-Code-chapter-and-verse/ff48a7a7-4145-4602-b5a5-4fbe1851fa50/scratchpad/gates/statute-eq-s39.transcript.txt`

### Sentences made vague by section number ban

The verifier bans model-written provision references. This forces the model to substitute vague placeholders when summarizing statutory cross-references:

- Question `era-s124`:
  - "However, the limit does not apply in cases where a person is regarded as unfairly dismissed by virtue of certain specified grounds." (Source specifies sections 100, 103A, 105(3), 105(6A)).
  - "This limit applies to any compensation awarded under specific sections or a compensatory award calculated in accordance with the relevant provisions." (Source specifies sections 117(1), 117(2), and 123).
  - "Additionally, the limit may be exceeded to the extent necessary to enable the award to fully reflect amounts specified as payable under other sections of the Act." (Source specifies sections 114(2)(a) and 115(2)(d)).

- Question `era-s94`:
  - "This right is subject to the provisions of the same Part of the Act and to the provisions of the Trade Union and Labour Relations (Consolidation) Act 1992." (Source specifies sections 108 to 110 of ERA and sections 237 to 239 of TULRCA).

- Question `era-s98`:
  - "Under the relevant legislation, a reason for dismissal falls within the statutory categories if it relates to the capability or qualifications of the employee..." (Source specifies "a reason falling within subsection (2)").

- Question `era-s123`:
  - "Where a redundancy payment exceeds the basic award payable but for specific provisions, that excess reduces the compensatory award." (Source specifies section 122(4)).

- Question `statute-eq-s39`:
  - "...unless making the offer contravenes specific provisions regarding direct or indirect discrimination." (Source specifies section 13, 14, or 19).

### Provider quota observations

Groq per-minute token budgets (8,000 tokens per minute) are quickly consumed by multi-window generation prompts. When Groq is rate-limited on tokens, the rotation falls back to Cloudflare. Because Cloudflare took most judge calls on long answers, the 40-call Cloudflare cap was reached at question 17.
