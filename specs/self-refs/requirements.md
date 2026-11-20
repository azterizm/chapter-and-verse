# Self references: requirements

Context: `specs/grounded-refs/` lets a model-written provision reference pass when the cited passage's text contains it. A provision's text never contains its own number, so a sentence that names the provision it cites still fails. Measured on 19 Nov in a live re-run of the latency gate (`SCRATCH/gates2/`): 7 of 13 generated answers ended in a refusal with reason `MODEL_CITATION`. Offline repro against the real ERA s.100(1) text, cited as P1:

| Sentence | Today |
|---|---|
| "Under section 100, an employee who is dismissed is regarded as unfairly dismissed [P1]." | fails, `MODEL_CITATION: section 100` |
| "Under subsection (1), the employee is regarded as unfairly dismissed [P1]." | fails, `MODEL_CITATION: subsection (1)` |
| "An employee is regarded as unfairly dismissed if the reason is one of those set out [P1]." | passes |

## R1 A reference to the cited provision itself is grounded

- R1.1 WHEN a model-written provision reference names the coordinate of a passage the sentence cites, or an ancestor of it within the same instrument (for P1 = `uk/ukpga/1996/18/s100/1`: section 100, s.100, section 100(1), subsection (1)), THEN the reference check SHALL pass it.
- R1.2 A reference to a sibling or descendant that is not cited and not in the cited text SHALL still fail (for P1 = s.100(1): "subsection (2)" fails unless P1's text contains it or a cited passage is s.100(2)).
- R1.3 A reference naming a different section of the same instrument SHALL still fail unless the cited text contains it (grounded-refs rule unchanged).
- R1.4 Relative references ("subsection (1)", "paragraph (a)") resolve against the cited passage's own section, never against another section.
- R1.5 All grounded-refs tests and the R1.3 to R1.5 rules of `specs/grounded-refs/requirements.md` keep passing.

## R2 Tests and regression

- R2.1 The three sentences above as tests (expected: pass, pass, pass), plus R1.2 to R1.4 cases on real ERA text.
- R2.2 Offline: for each rolled-back point in `SCRATCH/gates2/gates/*.transcript.txt` the drafts are not recorded, so instead write 10 realistic self-referencing sentences for ERA s.94, s.98, s.100, s.103A, s.111, s.119, s.123, s.124 and two SI regulations, cited to the right passage from the store, and show they pass; and 5 that cite a wrong section and show they fail. Write `SCRATCH/self-refs/regression.txt`.

`SCRATCH` = `$CV_SCRATCH`.
