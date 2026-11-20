# Live test fixes (20 Nov 2026): requirements

Found by the user testing https://chapterandverse.memonsystems.com.

## R1 Clarify keeps the provision
Asking "Employment Rights Act section 124" correctly returns a clarify event with candidates Employment Rights Act 1996 and Employment Rights Act 2025. Clicking "Employment Rights Act 1996" sent the bare instrument coordinate `uk/ukpga/1996/18`, so "section 124" was lost and the whole Act was answered.
- R1.1 WHEN the ambiguous query names a provision (the router's parsed citation has a `provision`) THEN each clarify candidate SHALL carry the coordinate of that provision inside the candidate instrument (for example `uk/ukpga/1996/18/s124`), when that provision exists in that instrument (check with the router or the store); otherwise the instrument coordinate as today. The label SHALL name the provision, for example "s.124 Employment Rights Act 1996".
- R1.2 Tests with the fixture router: the ERA 1996 candidate is `uk/ukpga/1996/18/s124`; a candidate instrument lacking the provision keeps the instrument coordinate.

## R2 "Cut short" wording
When providers become unavailable after at least one sentence, the page shows "Answer cut short. The model became unavailable before the answer was finished." In the live test the answer was in fact complete (it covered every subsection of s.124). The engine cannot know whether more was coming.
- R2.1 Change the notice wording to: title "Answer may be incomplete", message "The model became unavailable while writing. Every sentence shown was checked; there may have been more to say." in `engine/chapterverse/templates.py` and `web/src/text.ts`. Update the tests that assert the old wording.

## R3 Checks
Steering checks (Python) and `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build` in `web`.
