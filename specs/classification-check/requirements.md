# Classification check: requirements

Context: the Slice 1 latency gate found a wrong sentence that passed every check. Equality Act 2010 s.39(1) says "An employer (A) must not discriminate against a person (B)". The answer said "It is an offence for an employer to discriminate against a person ..." and the judge (Qwen on Cloudflare) marked it supported. Transcript: `SCRATCH/gates/statute-eq-s39.transcript.txt`. The verifier's deontic check covers modals (must, shall, may) but not a change of legal classification, such as a civil duty stated as a criminal offence.

## R1 Deterministic classification check

User story: as a user, I want a sentence that calls something an offence (or another criminal classification) to be rejected unless the cited text says so, so that a civil duty is never presented as a crime.

- R1.1 WHEN a sentence contains a criminal-classification term (at least: offence, offences, offender, criminal, crime, crimes, guilty, conviction, convicted, imprisonment, prosecution, prosecuted, indictment, indictable, summary conviction) AND none of the texts of the passages it cites contains a term of the same class THEN the check SHALL fail the sentence with reason code `CLASSIFICATION_SHIFT`, before any judge call.
- R1.2 Matching SHALL be case-insensitive, on word boundaries, and SHALL treat inflections in the list as one class (so "offence" in a sentence is grounded by "offences" or "commits an offence" in the passage).
- R1.3 A sentence that cites no passage and contains such a term SHALL fail with the same code.
- R1.4 WHEN the cited passage contains a term of the class THEN the check SHALL pass, including for negated sentences ("is not an offence") only if the passage contains the term; otherwise fail (strict).
- R1.5 The check SHALL be a pure function with no model call, deterministic, under 1 ms per sentence for a 300-character sentence and 8 passages of 1,500 characters (measure p99 over 1,000 runs and report it).
- R1.6 A failed sentence SHALL follow the existing rollback policy (inject the cited windows, then retry, then refusal), exactly like a reference failure in `chapterverse.audit.refs`.

## R2 Tests

- R2.1 The s.39(1) case: the sentence from the transcript against the real s.39(1) text fails; "An employer must not discriminate against a person in the arrangements for deciding to whom to offer employment." passes.
- R2.2 A real offence provision passes: pick one from the real data (for example a section that says "commits an offence" or "is guilty of an offence"), confirm it via the store, and put its text in the test.
- R2.3 Inflection, case, negation, no-citation and multi-passage cases.
- R2.4 An end-to-end test through `ChatContinuationGenerator` with the fake rotation and fake judge from the existing audit tests: the offence sentence is rolled back and the retry's correct sentence is emitted.

## R3 Regression

- R3.1 Re-run the 17 recorded gate answers offline: for every sentence in `SCRATCH/gates/*.json` (or the transcripts) with its cited passage texts from the same stream's `sources` event, report how many sentences the new check would fail. Expected: the s.39 sentence and no other supported sentence; list any other with its text. Write the result to `SCRATCH/classification-check/regression.txt`.

`SCRATCH` = `$CV_SCRATCH`.
