# Grounded references: requirements

Context: the generation prompt and `engine/chapterverse/audit/refs.py` fail any sentence in which the model writes a provision reference itself ("section 100", "subsection (1ZA)", "s.103A"). The rule exists so that the model never invents a citation. The Slice 1 latency gate (`reports/slice1-gates.md`, "Sentences made vague") showed the cost: the model writes "certain specified grounds" where ERA s.124(1A) lists "section 100, 103A, 105(3) or 105(6A)", and similar on s.94, s.98 and s.123. Transcripts: `SCRATCH/gates/*.transcript.txt`.

## R1 Allow references the cited text contains

User story: as a user, I want an answer to name the sections the cited provision itself names, so that the answer is as specific as the law.

- R1.1 WHEN a sentence contains a provision reference written by the model AND that same reference appears in the text of a passage the sentence cites THEN the reference check SHALL pass it. "The same reference" means the same normalised target: "section 100", "s.100" and "s 100" are one; "105(3)" in the passage grounds "section 105(3)" in the sentence; "subsection (1ZA)" in the passage grounds "subsection (1ZA)" in the sentence.
- R1.2 A reference not found in any cited passage's text SHALL still fail with its current code.
- R1.3 SI numbers, coordinates, instrument ids and URLs written by the model SHALL still fail, even when present in a passage.
- R1.4 An unknown or malformed passage id (`[P9]`, `(P1)`) SHALL still fail.
- R1.5 A list in the sentence ("sections 100, 103A or 105(3)") SHALL pass only if every item in it is grounded.
- R1.6 The rendered citation stays system-made from passage ids; R1.1 only stops a grounded reference in prose from failing the sentence.

## R2 Prompt

- R2.1 The generation prompt (`audit/prompt.py`) SHALL say that the model may name a section, subsection or paragraph only when the passage it cites names it, and must still cite with `[P#]`. `PROMPT_SHA256` changes accordingly; record the old and new values.
- R2.2 The prompt SHALL stay within 5% of its current token estimate for the ERA s.124 prompt.

## R3 Tests and regression

- R3.1 Unit tests for R1.1 to R1.5, using the real ERA s.124(1A) text ("section 100, 103A, 105(3) or 105(6A)").
- R3.2 Existing tests in `tests/test_audit_refs.py` and `tests/test_audit_prompt.py` keep passing, changed only where a test asserted the old strict rule for a now-grounded reference; list each changed test.
- R3.3 Offline regression over the gate transcripts: list every supported or rolled-back sentence whose reference verdict changes under the new rule, with its text. Write `SCRATCH/grounded-refs/regression.txt`.

`SCRATCH` = `$CV_SCRATCH`.
