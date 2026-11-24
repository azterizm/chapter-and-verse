# W6b: tasks (parallel with W6a)
Fix review-2.md item 1. Files you own: `web/src/components/Turn.tsx`, `QuoteBlock.tsx`, the W2 block of `web/src/text.ts`, `web/test/dom/answer.test.tsx`. Hiding the sources for a quote answer: if App.tsx decides where sources render, do NOT edit App.tsx; instead export a pure helper `isQuoteAnswer(turn)` from `Turn.tsx` (or a new `web/src/lib/answer.ts` you own) and say in your summary the one-line change App.tsx needs. Allowed shell commands: exactly as in tasks-1-foundation.md.
- [x] 1. Read review-2.md and design.md; item 1; update your tests.
- [x] 2. Run the four npm scripts. Print a short summary.
